"""FastAPI app for the browser-extension backend."""

# [INPUT]: 配置、MemoryManager、Database 与来源事件规范化器
# [OUTPUT]: create_app() 及浏览器/桌面 Web 共用的 FastAPI 路由
# [POS]: API 组合根，负责请求边界与事件入口，不在此复制来源解析规则
# [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import datetime as datetime_module
import inspect
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import time
import unicodedata
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, NoReturn, cast
from urllib.parse import parse_qsl, quote, urlparse, urlsplit, urlunsplit
from uuid import UUID

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.background import BackgroundTask

from openbiliclaw.api.models import (
    ActivityFeedItemOut,
    ActivityFeedResponse,
    AutostartApplyIn,
    AutostartConfigOut,
    AutostartStatusOut,
    BackendUpdateStatusOut,
    BangumiSourceConfigOut,
    BehaviorEventBatchIn,
    BilibiliConfigOut,
    BilibiliCookieIn,
    BilibiliCookieResponse,
    BilibiliSourceConfigOut,
    ChatIn,
    ChatTurnIn,
    ChatTurnListResponse,
    ChatTurnOut,
    CognitionUpdateSeenIn,
    CognitionUpdateSeenResponse,
    CognitionUpdateSummary,
    ConfigApplyStatusResponse,
    ConfigIssueOut,
    ConfigModelDiscoveryIn,
    ConfigModelDiscoveryResponse,
    ConfigResponse,
    ConfigServiceProbeIn,
    ConfigServiceProbeResponse,
    ConfigUpdateIn,
    ConfigUpdateResponse,
    ContentHistoryContextOut,
    ContentHistoryItemOut,
    ContentHistoryResponse,
    DelightAckIn,
    DelightAckResponse,
    DelightResponseIn,
    DialogueContextPreview,
    DiscoveryConfigOut,
    DouyinCookieIn,
    DouyinCookieResponse,
    DouyinSourceConfigOut,
    EmbeddingConfigOut,
    EventIngestResponse,
    EventReceiptOut,
    EventRejectedOut,
    ExtensionE2EAction,
    ExtensionE2EActionReportOut,
    ExtensionE2EActionStatus,
    ExtensionE2EEventMatchOut,
    ExtensionE2EPlatform,
    ExtensionE2EPlatformReportOut,
    ExtensionE2EResultIn,
    ExtensionE2ERunIn,
    ExtensionE2ERunOut,
    ExtensionE2ERunStatus,
    ExtensionNativeSaveE2EAuthorizationIn,
    ExtensionNativeSaveResultIn,
    FavoriteAddIn,
    FavoriteItem,
    FavoriteListResponse,
    FavoriteStateResponse,
    FeedbackIn,
    FeedbackResponse,
    GitHubSourceConfigOut,
    HealthResponse,
    InitPrerequisitesOut,
    InitStageOut,
    InitStatusOut,
    InsightFeedbackIn,
    InsightFeedbackResponse,
    LinuxdoLoginStateIn,
    LinuxdoLoginStateResponse,
    LinuxdoSourceConfigOut,
    LLMConfigOut,
    LLMInstanceConfigOut,
    LLMProviderConfigOut,
    LoggingConfigOut,
    ModuleLLMConfigOut,
    NetworkConfigOut,
    NotificationAckIn,
    NotificationAckResponse,
    PendingCognitionUpdateOut,
    PendingCognitionUpdateResponse,
    PendingDelightOut,
    PendingDelightResponse,
    PendingNotificationOut,
    PendingNotificationResponse,
    PlatformAvailabilityResponse,
    ProfileEditIn,
    ProfileSummaryResponse,
    ProjectStatsResponse,
    RecommendationAppendIn,
    RecommendationClickIn,
    RecommendationClickResponse,
    RecommendationListResponse,
    RecommendationOut,
    RecommendationRefreshResponse,
    RecommendationReshuffleIn,
    RecommendationReshuffleResponse,
    RedditCookieIn,
    RedditCookieResponse,
    RedditSourceConfigOut,
    RuntimeStatusResponse,
    SavedItemIn,
    SavedItemKeyIn,
    SavedItemStateResponse,
    SavedListItem,
    SavedListResponse,
    SavedSyncBatchResponse,
    SavedSyncConfigOut,
    SavedSyncItemResponse,
    SavedSyncRequest,
    SchedulerConfigOut,
    SoulConfigOut,
    SourceCredentialItem,
    SourceCredentialWriteIn,
    SourceCredentialWriteResponse,
    SourcesBrowserConfigOut,
    SourcesConfigOut,
    SourcesCredentialsResponse,
    SourceShareSuggestionIn,
    SourceShareSuggestionResponse,
    SourcesStatusResponse,
    SourceStatusItem,
    SourceVerifyResponse,
    StorageConfigOut,
    TailnetConfigOut,
    TwitterSourceConfigOut,
    UpdateApplyIn,
    UpdateCheckIn,
    UpdateStatusResponse,
    V2EXSourceConfigOut,
    WatchLaterAddIn,
    WatchLaterItem,
    WatchLaterListResponse,
    WatchLaterStateResponse,
    WeiboLoginStateIn,
    WeiboLoginStateResponse,
    WeiboSourceConfigOut,
    XCookieIn,
    XCookieResponse,
    XhsLoginStateIn,
    XhsLoginStateResponse,
    XiaohongshuSourceConfigOut,
    XStatusResponse,
    YoutubeSourceConfigOut,
    ZhihuLoginStateIn,
    ZhihuLoginStateResponse,
    ZhihuSourceConfigOut,
    validate_saved_item_key,
)
from openbiliclaw.diagnostics_alerts import get_diagnostics_alert_buffer
from openbiliclaw.discovery.temporal import (
    evaluate_temporal_eligibility,
    is_complete_temporal_evidence_marker,
)
from openbiliclaw.llm.base import safe_llm_failure_message
from openbiliclaw.runtime import embedding_progress
from openbiliclaw.runtime.dialogue_reply_scheduler import (
    DialogueExecutionCoordinator,
    DurableChatReplyScheduler,
    TerminalChatReplyError,
)
from openbiliclaw.runtime.event_ingress import EventIngressService
from openbiliclaw.runtime.feedback_scheduler import FeedbackBatchScheduler
from openbiliclaw.runtime.image_cache import (
    CoverFetchError,
    cleanup_image_cache,
    image_log_identity,
    save_extension_cover,
)
from openbiliclaw.runtime.image_fetch import ImageFetchCoordinator
from openbiliclaw.runtime.keyword_fetch import (
    mark_keyword_terminal_from_xhs_task,
    requeue_keyword_from_xhs_rate_limit,
    source_keyword_id_from_xhs_task,
)
from openbiliclaw.saved_sync.extension_broker import (
    ExtensionNativeSaveResultIn as BrokerExtensionNativeSaveResultIn,
)
from openbiliclaw.saved_sync.identity import make_item_key
from openbiliclaw.saved_sync.models import (
    NATIVE_SAVE_STATUSES,
    NativeSaveResult,
    NativeSaveStatus,
    SavedItemInput,
    SavedListKind,
    SavedSyncBatchResult,
)
from openbiliclaw.soul.dislike_writeback import (
    apply_new_dislikes,
    topics_for_confirmed_avoidance,
)
from openbiliclaw.sources.platforms import (
    CANONICAL_SOURCE_FAMILIES,
    normalize_source_platform,
    overseas_network_hint,
    requires_overseas_network,
)
from openbiliclaw.sources.platforms import (
    infer_source_platform_from_url as _registry_infer_source_platform_from_url,
)
from openbiliclaw.storage.database import CONTENT_HISTORY_RETENTION_DAYS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from openbiliclaw.api.source_auth.contract import SourceAuthContract
    from openbiliclaw.api.source_auth.write import CredentialWriteOutcome
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueDispatchResult,
        DialogueJob,
        DialogueJobKind,
        DialogueJobResult,
    )

logger = logging.getLogger(__name__)

# A local Ollama chat probe can spend ~31 seconds in its documented cold-load
# retry window before the model answers. The previous 30-second API cap killed
# that legitimate retry at the boundary and made the first settings-page test
# fail while an immediate retry passed. Keep a finite control-plane budget, but
# leave enough headroom for cold local providers.
_CONFIG_LLM_PROBE_MAX_TIMEOUT_SECONDS = 120.0


def _config_llm_probe_timeout_seconds(configured_timeout: object) -> float:
    """Clamp one settings-page LLM probe to a finite cold-start-safe budget."""
    try:
        parsed = float(str(configured_timeout or 300))
    except (TypeError, ValueError):
        parsed = 300.0
    return min(max(parsed, 10.0), _CONFIG_LLM_PROBE_MAX_TIMEOUT_SECONDS)


_CONFIG_SAVE_LOCK = asyncio.Lock()
_MIGRATION_TRANSFER_LOCK = asyncio.Lock()
_fire_and_forget_tasks: set[asyncio.Task[None]] = set()
_SQLITE_SIGNED_INTEGER_MAX = (1 << 63) - 1
_CONTENT_HISTORY_CURSOR_VERSION = 1
_CONTENT_HISTORY_CURSOR_MAX_LENGTH = 32768
_CONTENT_HISTORY_CURSOR_ITEM_KEY_MAX_LENGTH = 2048


class _MigrationArchiveStreamingResponse(StreamingResponse):
    """Always erase a plaintext export, including ASGI send-start failures."""

    def __init__(
        self,
        *args: Any,
        cleanup_directory: Path,
        release_callback: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> None:
        self._cleanup_directory = cleanup_directory
        self._release_callback = release_callback
        super().__init__(*args, **kwargs)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Starlette does not necessarily close an async body iterator when
            # an ASGI 2.3 client disconnects while ``send`` is suspended. On
            # Windows the still-open archive then prevents rmtree from removing
            # the plaintext file. Close first, delete second, and only then let
            # another export acquire the transfer lock.
            close_iterator = getattr(self.body_iterator, "aclose", None)
            if callable(close_iterator):
                try:
                    close_result = close_iterator()
                    if inspect.isawaitable(close_result):
                        await close_result
                except (Exception, asyncio.CancelledError):
                    logger.warning("Failed to close migration archive response iterator")
            try:
                shutil.rmtree(self._cleanup_directory, ignore_errors=True)
            finally:
                release_callback = self._release_callback
                self._release_callback = None
                if release_callback is not None:
                    release_callback()


def apply_retraction_db_marks(database: Any, events: list[dict[str, Any]]) -> int:
    """Deprecated best-effort retraction projection compatibility wrapper.

    HTTP ingress no longer calls this hook: the generic durable event owner
    performs the strict projection before advancing its cursor.  Keep the old
    import surface for embedders while delegating to the same whitelist and
    timestamp rules.
    """
    from openbiliclaw.sources.event_format import (
        RETRACTABLE_ACTIONS,
        parse_event_timestamp,
    )

    total = 0
    for event in events:
        event_type = str(event.get("event_type") or event.get("type") or "").strip().lower()
        metadata = event.get("metadata")
        if event_type != "feedback" or not isinstance(metadata, dict):
            continue
        if str(metadata.get("feedback_type") or "").strip().lower() != "retraction":
            continue
        action = str(metadata.get("retracted_action") or "").strip().lower()
        if action not in RETRACTABLE_ACTIONS:
            logger.warning("Skipping out-of-whitelist retracted_action %r", action)
            continue
        url = str(event.get("url") or "")
        retraction_at = parse_event_timestamp(metadata)
        if not url or retraction_at is None:
            continue
        try:
            total += int(
                database.mark_positive_events_retracted(
                    [url],
                    action,
                    retraction_at=retraction_at,
                )
                or 0
            )
        except Exception:
            logger.warning("Legacy retraction DB projection failed", exc_info=True)
    return total


_HYPOTHESIS_CARD_ACTIONS = ("confirm", "reject", "discuss", "defer")
# Settled for good — no reconcile, no further action. ``revised`` belongs here:
# the user replaced the wording and accepted the correction, which is as final
# as a plain confirm/reject and must not read back as 已标记不准.
_TERMINAL_CARD_STATES = frozenset({"confirmed", "rejected", "revised"})
# First-round calibration (2026-07-22): 0.60 is the lower edge at which an
# unvalidated hypothesis is concrete enough to ask about; open confusions use
# 0.50 because their explicit contradiction is already stronger evidence.
# The badge/list is deliberately capped at three to keep the entry lightweight.
_PENDING_HYPOTHESIS_MIN_CONFIDENCE = 0.60
_PENDING_CONFUSION_MIN_CONFIDENCE = 0.50
_PENDING_CONFIRMATION_LIMIT = 3
# Issue #213: a ``no_provider`` failure is config-shaped (empty resolved module
# route / global chain), so retrying it forever only parks the durable turn on
# the infinite "thinking" spinner and head-of-line blocks every later turn.
# Three fast failures (≈3s with the scheduler's 1s→2s backoff) escalate the
# turn to a terminal failed state with remediation copy, while transient
# windows (a quick settings toggle, hot reload) still get those retries to
# heal. Recalibrate only alongside the escalation test in
# tests/test_durable_chat_reply_api.py.
_CHAT_NO_PROVIDER_TERMINAL_ATTEMPTS = 3
# Seats reserved for confusions when any qualify. The two kinds score confidence
# on opposite scales: a hypothesis' confidence means "how sure am I this is
# true" (higher = more worth asking), while a confusion's
# interpretation_confidence means "how sure am I of my guess" — and the prompt
# explicitly says only a LOW-confidence reading should become a confusion. Sorting
# both into one descending list therefore buries the least-understood items
# last, and a real profile carries hundreds of ≥0.60 hypotheses (334 on the
# author's own data, top-3 cutoff at 0.76), so confusions could never surface in
# the user-initiated list at all. Unused seats fall back to hypotheses.
_PENDING_CONFUSION_RESERVED_SLOTS = 1
# First-round calibration (2026-07-24): confirmation turn creation is a local
# SQLite effect budgeted at 1 second. A 30x fence distinguishes that live
# claim→create window from a crashed creator before orphan recovery may run.
_CONFUSION_ORPHAN_CLAIM_MIN_AGE_SECONDS = 30.0
# First-round calibration (2026-07-22): at most two unsolicited entries per
# day, while a particular object stays quiet for three days. Recalibrate from
# observed defer rates after the first production month.
_CONFIRMATION_GLOBAL_COOLDOWN_HOURS = 12
# First-round calibration (2026-07-22): three days matches the existing
# object-level ask budget and makes "以后再说" durable without becoming a
# permanent dismissal. Recalibrate after the first production month.
_CONFIRMATION_OBJECT_COOLDOWN_HOURS = 72
_RUNTIME_STREAM_HEARTBEAT_SECONDS = 20.0
_DIALOGUE_EXECUTION_DRAIN_TIMEOUT_SECONDS = 1500.0


@dataclass(slots=True)
class _QueuedConfigApply:
    """One persisted config revision waiting for a safe runtime handoff."""

    revision: int
    config: Any
    saved_path: Path
    run_post_reload_llm_work: bool
    restart_required: bool = False


# Guided-init owner-lease heartbeat period. A stage can spend minutes inside one
# provider call, so ``touch()`` proves that the wrapper/event loop still owns the
# run. It deliberately does not advance the separate useful-progress clock.
_INIT_HEARTBEAT_INTERVAL_SECONDS = 30.0


async def _run_init_heartbeat(
    coordinator: Any, run_id: str, *, interval: float = _INIT_HEARTBEAT_INTERVAL_SECONDS
) -> None:
    """Refresh the init owner lease during long, silent stages.

    ``touch()`` publishes no SSE event and does not claim useful progress. A
    heartbeat failure must not kill init, so it is swallowed at WARNING.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await coordinator.touch(run_id)
        except Exception:
            logger.warning("init heartbeat touch failed for run %s", run_id, exc_info=True)


# /api/health embedding readiness: cache the raw live-probe outcome so Docker
# healthchecks and popup re-polls don't hit the embedding provider on every
# call. Success caches for 30s; failure/timeout for 8s so a freshly-fixed or
# just-finished cold load greens quickly. The probe itself is capped at 15s,
# but local bge-m3 cold loads were observed at 16-29s on 2026-07-11. Ordinary
# health therefore treats only a loopback-Ollama timeout as cold-loading and
# optimistically available; guided init remains strict until a real vector
# succeeds. Explicit False/errors still fail everywhere.
_EMBEDDING_READY_TTL_SECONDS = 30.0
_EMBEDDING_FAIL_TTL_SECONDS = 8.0
_EMBEDDING_PROBE_TIMEOUT_SECONDS = 15.0
_EmbeddingProbeOutcome = Literal["ready", "failed", "timed_out"]
_LAN_IP_TTL_SECONDS = 30.0
_AUTO_REPLENISH_DEBOUNCE_SECONDS = 30.0
_FEEDBACK_BATCH_DEBOUNCE_SECONDS = 5.0
# GET /api/recommendations serves from the pool when the unprocessed history
# window is thinner than this — a first page of 2-3 cards reads as "broken"
# even though the pool has stock (issue #81 挤牙膏首载).
_FIRST_PAGE_TOPUP_FLOOR = 10
# The thin-history top-up is a side-effecting write on a GET, so debounce it:
# if serve() cannot actually raise the row count (all pool items filtered),
# polling clients must not re-run it every few seconds. An empty history
# (fresh install) bypasses the debounce — matching the original bootstrap.
_FIRST_PAGE_TOPUP_DEBOUNCE_SECONDS = 30.0
# Collapse simultaneous boot reads from restored/stale browser tabs. The cache
# is intentionally tiny: it is a load-shedding single-flight window, not a
# user-visible freshness policy. Mutating recommendation routes invalidate it.
_RECOMMENDATION_SNAPSHOT_TTL_SECONDS = 1.0


def _recommendation_snapshot_rows_and_expiry(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    monotonic_now: float | None = None,
) -> tuple[list[dict[str, Any]], float]:
    """Recheck rows and cap cache life at the earliest temporal transition."""

    # Anchor the cache deadline before reading wall time.  Sampling these in
    # the opposite order would add any scheduling delay between the two reads
    # to a near-transition card's cache lifetime and could cross its review or
    # hard-expiry boundary.
    effective_monotonic = time.monotonic() if monotonic_now is None else monotonic_now
    effective_now = now or datetime.now(UTC)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=UTC)
    expires_at = effective_monotonic + _RECOMMENDATION_SNAPSHOT_TTL_SECONDS
    eligible: list[dict[str, Any]] = []
    for row in rows:
        decision = evaluate_temporal_eligibility(
            temporal_class=row.get("temporal_class", "unknown"),
            temporal_confidence=row.get("temporal_confidence", 0.0),
            published_at=row.get("published_at", ""),
            temporal_validity_mode=row.get("temporal_validity_mode", "none"),
            temporal_valid_until=row.get("temporal_valid_until", ""),
            temporal_scope=row.get("temporal_scope", "none"),
            temporal_evidence=row.get("temporal_evidence", ""),
            temporal_state=row.get("temporal_state", "unknown"),
            temporal_next_review_at=row.get("temporal_next_review_at", ""),
            temporal_evaluated_at=row.get("temporal_evaluated_at", ""),
            temporal_policy_version=row.get("temporal_policy_version", "v1"),
            evidence_complete=is_complete_temporal_evidence_marker(
                row.get("temporal_evidence_complete")
            ),
            now=effective_now,
        )
        if not decision.eligible:
            continue
        eligible.append(row)
        if not decision.trigger_at:
            continue
        try:
            trigger = datetime_module.datetime.fromisoformat(
                decision.trigger_at.replace("Z", "+00:00")
            )
            if trigger.tzinfo is None:
                continue
            remaining_seconds = max(
                0.0,
                (trigger.astimezone(effective_now.tzinfo) - effective_now).total_seconds(),
            )
        except (OverflowError, OSError, ValueError):
            continue
        expires_at = min(expires_at, effective_monotonic + remaining_seconds)
    return eligible, expires_at


# Canonical home is openbiliclaw.sources.x_auth (mirrors douyin_auth);
# re-exported here because callers historically imported from api.app.
#
# ``X_REQUIRED_COOKIE_NAMES`` used to be re-exported here too. It no longer is:
# which cookie names X requires is now stated once, in the write path's
# ``CREDENTIAL_SPECS``, and a second copy in this file is exactly how a check
# and its endpoint drift apart.
from openbiliclaw.sources.x_auth import (  # noqa: E402, F401
    XCookieManager,
    resolve_x_cookie,
)

SOURCE_LABELS = {
    "feedback": "推荐反馈",
    "chat": "聊天",
    "profile_refresh": "聚合观察",
}

_SOURCE_SHARE_ORDER = (
    "bilibili",
    "xiaohongshu",
    "douyin",
    "youtube",
    "twitter",
    "github",
    "zhihu",
    "reddit",
    "bangumi",
    "linuxdo",
    "v2ex",
    "weibo",
)
# Unknown/unregistered platform slugs are preserved in the database for future
# expansion, but they must not silently disappear from source-share counts.
_SOURCE_COUNT_ORDER = _SOURCE_SHARE_ORDER + ("unknown",)
_INIT_SOURCE_ORDER = (
    "bilibili",
    "xiaohongshu",
    "douyin",
    "youtube",
    "twitter",
    "github",
    "zhihu",
    "reddit",
    "bangumi",
    "linuxdo",
    "v2ex",
    "weibo",
)
_PROBE_MODES = {"near", "lateral", "bridge", "wildcard"}
_PROBE_CHALLENGE_MODES = {"lateral", "bridge", "wildcard"}

_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_BENCHMARK_NETWORK = ipaddress.ip_network("198.18.0.0/15")
# Cover-image fetch/whitelist constants live in openbiliclaw.runtime.image_cache
# (shared by the proxy route and the prefetch sweep). Only the disk-cache age cap
# is referenced directly from here, by the startup cleanup call.
_IMAGE_CACHE_MAX_AGE_DAYS = 30

_E2E_STATE_CHANGING_ACTIONS = frozenset({"like", "favorite", "follow", "repost", "bookmark"})
_E2E_DEFAULT_SAFE_ACTIONS: tuple[ExtensionE2EAction, ...] = (
    "snapshot",
    "scroll",
    "click",
    "share",
)
_E2E_ACTION_EVENT_TYPES: dict[ExtensionE2EAction, frozenset[str]] = {
    "snapshot": frozenset({"snapshot"}),
    "scroll": frozenset({"scroll"}),
    "click": frozenset({"click"}),
    "share": frozenset({"click"}),
    "like": frozenset({"like", "favorite"}),
    "favorite": frozenset({"favorite", "bookmark"}),
    "follow": frozenset({"follow"}),
    "repost": frozenset({"share", "repost"}),
    "bookmark": frozenset({"bookmark", "favorite"}),
}
_NATIVE_SAVE_E2E_CONTENT_TYPES: dict[str, frozenset[str]] = {
    "youtube": frozenset({"video"}),
    "xiaohongshu": frozenset({"note", "video"}),
    "douyin": frozenset({"aweme", "video"}),
    "twitter": frozenset({"tweet", "status"}),
    "zhihu": frozenset({"question", "answer", "article"}),
    "reddit": frozenset({"post", "comment"}),
}


def _native_save_e2e_content_id_from_url(
    platform: str,
    content_type: str,
    value: str,
) -> str:
    """Return the exact identity accepted by the production content executor."""
    if (
        not value
        or value != value.strip()
        or len(value) > 2048
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        return ""
    try:
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or hostname.endswith(".")
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return ""

    def host_is_or_subdomain(*hosts: str) -> bool:
        return any(hostname == host or hostname.endswith(f".{host}") for host in hosts)

    if platform == "youtube":
        if hostname == "youtu.be":
            if parsed.query:
                return ""
            match = re.fullmatch(r"/([A-Za-z0-9_-]{11})/?", parsed.path)
            return match.group(1) if match else ""
        if hostname not in {"youtube.com", "www.youtube.com"}:
            return ""
        if parsed.path == "/watch":
            match = re.fullmatch(r"v=([A-Za-z0-9_-]{11})", parsed.query)
            return match.group(1) if match else ""
        if parsed.query:
            return ""
        match = re.fullmatch(r"/shorts/([A-Za-z0-9_-]{11})/?", parsed.path)
        return match.group(1) if match else ""
    if parsed.query and platform != "xiaohongshu":
        return ""
    if platform == "xiaohongshu":
        if not host_is_or_subdomain("xiaohongshu.com"):
            return ""
        if parsed.query:
            try:
                query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            except ValueError:
                return ""
            if (
                any(key not in {"xsec_token", "xsec_source"} or not item for key, item in query)
                or len({key for key, _item in query}) != len(query)
                or "xsec_token" not in {key for key, _item in query}
            ):
                return ""
        match = re.fullmatch(
            r"/(?:explore|discovery/item|search_result)/([A-Za-z0-9_-]+)/?",
            parsed.path,
        )
        return match.group(1) if match else ""
    if platform == "douyin":
        if not host_is_or_subdomain("douyin.com"):
            return ""
        match = re.fullmatch(r"/video/([A-Za-z0-9_-]+)/?", parsed.path)
        return match.group(1) if match else ""
    if platform == "twitter":
        if not host_is_or_subdomain("x.com", "twitter.com"):
            return ""
        match = re.fullmatch(r"/(?:i|[^/]+)/status/([0-9]+)/?", parsed.path)
        return match.group(1) if match else ""
    elif platform == "zhihu":
        if hostname not in {"zhihu.com", "www.zhihu.com", "zhuanlan.zhihu.com"}:
            return ""
        if content_type == "article":
            match = re.fullmatch(r"/p/([0-9]+)/?", parsed.path)
            return f"article:{match.group(1)}" if match else ""
        if hostname not in {"zhihu.com", "www.zhihu.com"}:
            return ""
        if content_type == "question":
            match = re.fullmatch(r"/question/([0-9]+)/?", parsed.path)
            return f"question:{match.group(1)}" if match else ""
        if content_type == "answer":
            match = re.fullmatch(r"/question/[0-9]+/answer/([0-9]+)/?", parsed.path)
            return f"answer:{match.group(1)}" if match else ""
    elif platform == "reddit":
        if not host_is_or_subdomain("reddit.com", "redd.it"):
            return ""
        parts = [part.lower() for part in parsed.path.split("/") if part]
        if content_type == "post" and host_is_or_subdomain("redd.it"):
            if len(parts) == 1 and re.fullmatch(r"[a-z0-9]+", parts[0]):
                return f"t3_{parts[0]}"
            return ""
        try:
            index = parts.index("comments")
        except ValueError:
            return ""
        if content_type == "post" and len(parts) > index + 1:
            post_id = parts[index + 1]
            return f"t3_{post_id}" if re.fullmatch(r"[a-z0-9]+", post_id) else ""
        if content_type == "comment" and len(parts) > index + 3:
            comment_id = parts[index + 3]
            return f"t1_{comment_id}" if re.fullmatch(r"[a-z0-9]+", comment_id) else ""
    return ""


def _native_save_e2e_membership_matches(
    authorization: ExtensionNativeSaveE2EAuthorizationIn,
    item: SavedItemInput,
    route: object,
) -> bool:
    content_type = item.content_type.strip()
    if (
        item.platform != authorization.platform
        or item.content_id != authorization.content_id
        or content_type not in _NATIVE_SAVE_E2E_CONTENT_TYPES[authorization.platform]
        or _native_save_e2e_content_id_from_url(
            authorization.platform,
            content_type,
            item.content_url,
        )
        != authorization.content_id
    ):
        return False
    return (
        getattr(route, "requested_action", None) == authorization.action
        and getattr(route, "resolved_action", None)
        == (
            authorization.action
            if authorization.platform == "youtube" or authorization.action == "favorite"
            else "favorite"
        )
        and getattr(route, "resolved_target", None) == authorization.expected_target
    )


@dataclass
class _ExtensionE2ERunState:
    run_id: str
    token: str
    started_at: float
    after_event_id: int
    expected_actions: dict[ExtensionE2EPlatform, list[ExtensionE2EAction]]
    event: asyncio.Event
    native_save_authorization: ExtensionNativeSaveE2EAuthorizationIn | None = None
    extension_result: ExtensionE2EResultIn | None = None
    error: str = ""


def _default_route_ip() -> str | None:
    """Return the IPv4 address selected for outbound traffic, if usable."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.1)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            return str(ip) if ip else None
    except Exception:
        return None


def _default_route_ipv6() -> str | None:
    """Return the IPv6 address selected for outbound traffic, if usable."""
    if not socket.has_ipv6:
        return None
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.1)
            sock.connect(("2001:4860:4860::8888", 80))
            ip = sock.getsockname()[0]
            return str(ip) if ip else None
    except Exception:
        return None


def _interface_ipv4_candidates() -> list[str]:
    """Best-effort local IPv4 enumeration without extra dependencies."""
    commands: list[list[str]]
    if os.name == "nt":
        commands = [["ipconfig"]]
    else:
        commands = [["ifconfig"], ["ip", "-4", "addr", "show", "scope", "global"]]

    candidates: list[str] = []
    seen: set[str] = set()
    for command in commands:
        try:
            if os.name == "nt":
                proc = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=2,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                proc = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=2,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0:
            continue
        for ip in re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", proc.stdout):
            if ip not in seen:
                candidates.append(ip)
                seen.add(ip)
        if candidates:
            break
    return candidates


def _interface_ipv6_candidates() -> list[str]:
    """Best-effort global/ULA IPv6 enumeration without extra dependencies."""
    if not socket.has_ipv6:
        return []
    commands = (
        [["ipconfig"]]
        if os.name == "nt"
        else [["ifconfig"], ["ip", "-6", "addr", "show", "scope", "global"]]
    )
    candidates: list[str] = []
    seen: set[str] = set()
    for command in commands:
        try:
            if os.name == "nt":
                proc = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=2,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                proc = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=2,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0:
            continue
        for token in re.findall(
            r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f:]*"
            r"(?:%[\w.-]+)?(?:/\d+)?",
            proc.stdout,
        ):
            candidate = token.split("/", 1)[0].split("%", 1)[0]
            try:
                addr = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if isinstance(addr, ipaddress.IPv6Address) and candidate not in seen:
                candidates.append(candidate)
                seen.add(candidate)
        if candidates:
            break
    return candidates


def _is_rfc1918_ipv4(addr: ipaddress.IPv4Address) -> bool:
    return any(addr in network for network in _RFC1918_NETWORKS)


def _usable_lan_candidate(ip: str) -> tuple[bool, bool]:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return (False, False)
    if not isinstance(addr, ipaddress.IPv4Address):
        return (False, False)
    if (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr in _BENCHMARK_NETWORK
    ):
        return (False, False)
    return (True, _is_rfc1918_ipv4(addr))


def _usable_ipv6_lan_candidate(ip: str) -> tuple[bool, bool]:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return (False, False)
    if (
        not isinstance(addr, ipaddress.IPv6Address)
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.ipv4_mapped is not None
        or not (addr.is_private or addr.is_global)
    ):
        return (False, False)
    return (True, addr.is_private)


def _detect_lan_ip() -> str | None:
    """Return a likely phone-reachable LAN IPv4 or IPv6 address.

    UDP default-route detection can return VPN/TUN addresses such as
    198.18.0.1 on macOS. Prefer RFC1918 interface addresses and only use
    the default-route result when it is not a benchmark / loopback address.
    IPv4 remains preferred for compatibility; an IPv6 ULA/global address is
    returned when the machine has no usable IPv4 LAN address.
    """
    candidates = _interface_ipv4_candidates()
    route_ip = _default_route_ip()
    if route_ip:
        candidates.append(route_ip)

    fallback: str | None = None
    for candidate in candidates:
        usable, rfc1918 = _usable_lan_candidate(candidate)
        if not usable:
            continue
        if rfc1918:
            return candidate
        if fallback is None:
            fallback = candidate
    if fallback is not None:
        return fallback

    ipv6_candidates = _interface_ipv6_candidates()
    route_ipv6 = _default_route_ipv6()
    if route_ipv6:
        ipv6_candidates.append(route_ipv6)
    ipv6_fallback: str | None = None
    for candidate in ipv6_candidates:
        usable, ula = _usable_ipv6_lan_candidate(candidate)
        if not usable:
            continue
        if ula:
            return candidate
        if ipv6_fallback is None:
            ipv6_fallback = candidate
    return ipv6_fallback


_RESETTABLE_CONFIG_FIELDS = {
    "llm.openai.api_key": ("llm", "openai", "api_key"),
    "llm.claude.api_key": ("llm", "claude", "api_key"),
    "llm.gemini.api_key": ("llm", "gemini", "api_key"),
    "llm.deepseek.api_key": ("llm", "deepseek", "api_key"),
    "llm.openrouter.api_key": ("llm", "openrouter", "api_key"),
    "llm.orcarouter.api_key": ("llm", "orcarouter", "api_key"),
    "llm.openai_compatible.api_key": ("llm", "openai_compatible", "api_key"),
    "llm.embedding.api_key": ("llm", "embedding", "api_key"),
}


def _config_backup_path(config_path: Path) -> Path:
    return config_path.with_name(f"{config_path.name}.bak")


def _snapshot_config_file(config_path: Path) -> Path | None:
    if not config_path.exists():
        return None
    backup_path = _config_backup_path(config_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_path, backup_path)
    return backup_path


def _restore_config_snapshot(backup_path: Path, config_path: Path) -> None:
    shutil.copy2(backup_path, config_path)


def _validate_llm_buildable(cfg: Any, base_issues: list[Any]) -> list[Any]:
    from openbiliclaw.config import ConfigIssue
    from openbiliclaw.llm.registry import RegistryBuildError, build_llm_registry

    issues = list(base_issues)
    try:
        build_llm_registry(cfg)
    except RegistryBuildError as exc:
        issues.append(
            ConfigIssue(
                field="llm",
                message=f"LLM registry would fail to build: {exc}",
                severity="blocking",
            )
        )
    return issues


def _posture_gate_enforce_issue(cfg: Any, database: Any | None) -> Any | None:
    """DB-backed save-time guard for ``soul.posture_gate_mode = enforce``.

    Reads shadow-judgement statistics from the ledger and defers to the pure
    :func:`posture_gate_enforce_readiness_issue` evaluator. A missing database
    (or a stats read failure) is conservatively treated as "no shadow data" so
    a premature enforce is rejected rather than silently accepted.
    """
    from openbiliclaw.config import posture_gate_enforce_readiness_issue

    if str(getattr(cfg.soul, "posture_gate_mode", "")).strip().lower() != "enforce":
        return None
    stats: dict[str, Any] = {"earliest_valid_at": "", "valid_count_14d": 0, "valid_count_7d": 0}
    getter = getattr(database, "posture_gate_shadow_stats", None) if database is not None else None
    if callable(getter):
        try:
            fetched = getter()
            if isinstance(fetched, dict):
                stats = fetched
        except Exception:
            logger.warning("posture_gate_shadow_stats read failed; treating as no data")
    return posture_gate_enforce_readiness_issue(
        cfg,
        earliest_valid_at=str(stats.get("earliest_valid_at", "")),
        valid_count_14d=int(stats.get("valid_count_14d", 0)),
        valid_count_7d=int(stats.get("valid_count_7d", 0)),
    )


def _count_events_by_source_platform(database: Any) -> dict[str, int]:
    """Count stored behavior events by normalized source platform.

    Unknown or unregistered platform slugs are aggregated under ``"unknown"``
    so they are not silently dropped from the source-share suggestion.
    """

    def _bucket(source: object) -> str:
        source_key = _normalize_source_platform(source)
        if source_key in _SOURCE_SHARE_ORDER:
            return source_key
        return "unknown"

    counter = {source: 0 for source in _SOURCE_COUNT_ORDER}
    if hasattr(database, "count_events_by_source_platform"):
        raw_counts = database.count_events_by_source_platform()
        if isinstance(raw_counts, dict):
            for source, count in raw_counts.items():
                counter[_bucket(source)] += int(count)
            return {source: counter.get(source, 0) for source in _SOURCE_COUNT_ORDER}

    rows: list[dict[str, Any]] = []
    if hasattr(database, "conn"):
        try:
            cursor = database.conn.execute("SELECT source_platform, metadata FROM events")
            rows = [dict(row) for row in cursor.fetchall()]
        except Exception:
            # Keep injected/legacy databases usable before the additive source
            # columns have been migrated.
            try:
                cursor = database.conn.execute("SELECT metadata FROM events")
                rows = [dict(row) for row in cursor.fetchall()]
            except Exception:
                rows = []
    elif hasattr(database, "get_recent_events"):
        try:
            rows = list(database.get_recent_events(limit=10000))
        except Exception:
            rows = []

    for row in rows:
        metadata = row.get("metadata", {})
        if isinstance(metadata, str):
            try:
                import json as _json

                metadata = _json.loads(metadata) if metadata else {}
            except Exception:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        source = (
            str(row.get("source_platform") or "").strip()
            or str(metadata.get("source_platform") or "").strip()
        )
        source = source or "bilibili"
        counter[_bucket(source)] += 1
    return {source: counter.get(source, 0) for source in _SOURCE_COUNT_ORDER}


def _select_init_platforms(enabled: set[str], selected: set[str] | None) -> set[str]:
    """Effective platform sources for a guided-init run.

    ``enabled`` is the config-enabled set; ``selected`` is the extension's
    per-run checkbox choice (``None`` when no selection was sent — CLI / legacy
    clients — meaning "use everything enabled"). A sent selection is an
    explicit local opt-in for those sources, not just a filter over old config.
    Bilibili flows through here like every other source (v0.3.118+): legacy
    clients keep their config-enabled behaviour, but deselecting it skips the
    B站 fetch.
    """
    if selected is None:
        return {
            normalized
            for source in enabled
            if (normalized := _normalize_init_source_key(source)) in _INIT_SOURCE_ORDER
        }
    return {
        normalized
        for source in selected
        if (normalized := _normalize_init_source_key(source)) in _INIT_SOURCE_ORDER
    }


def _normalize_init_source_key(source: object) -> str:
    source_key = str(source or "").strip().lower()
    if not source_key:
        return ""
    return _normalize_source_platform(source_key)


def _init_crash_detail(exc: BaseException) -> str:
    """One-line, length-capped exception summary for guided-init failures.

    Persisted with the ``internal_error`` reason and surfaced through
    ``GET /api/init-status`` so a community user can report the actual cause
    without digging through server logs (field report 2026-07-05: the generic
    「初始化过程中出错了」left the failure undiagnosable from the UI).

    An LLM-shaped failure (moderation refusal / exhausted providers / rate
    limit) is rewritten into a human-readable reason so the page shows advice
    instead of a raw ``InternalServerError: 非常抱歉…`` traceback fragment.
    """
    from openbiliclaw.llm.base import describe_llm_failure

    llm_reason = describe_llm_failure(exc)
    if llm_reason:
        return llm_reason[:300]
    lines = str(exc).strip().splitlines()
    message = lines[0].strip() if lines else ""
    text = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    return text[:300]


def _normalize_source_platform(source: object) -> str:
    return normalize_source_platform(source, default="bilibili")


def _infer_source_platform_from_url(url: object) -> str:
    return _registry_infer_source_platform_from_url(url)


def _extension_e2e_actions_for_request(
    payload: ExtensionE2ERunIn,
) -> dict[ExtensionE2EPlatform, list[ExtensionE2EAction]]:
    actions_by_platform: dict[ExtensionE2EPlatform, list[ExtensionE2EAction]] = {}
    seen_platforms: set[ExtensionE2EPlatform] = set()
    for platform in payload.platforms:
        if platform in seen_platforms:
            continue
        seen_platforms.add(platform)
        requested_actions = (
            payload.actions[platform]
            if platform in payload.actions
            else list(_E2E_DEFAULT_SAFE_ACTIONS)
        )
        deduped: list[ExtensionE2EAction] = []
        seen_actions: set[ExtensionE2EAction] = set()
        for action in requested_actions:
            if action in seen_actions:
                continue
            seen_actions.add(action)
            deduped.append(action)
        actions_by_platform[platform] = deduped
    return actions_by_platform


def _event_row_id(row: dict[str, Any]) -> int | None:
    try:
        event_id = int(row.get("id", 0) or 0)
    except (TypeError, ValueError):
        return None
    return event_id if event_id > 0 else None


def _event_row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata", {})
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata) if metadata else {}
        except Exception:
            parsed = {}
        metadata = parsed
    return metadata if isinstance(metadata, dict) else {}


def _durable_ingress_row(
    ctx: Any,
    *,
    event_id: int,
    inserted: bool,
    submitted_event: dict[str, Any],
) -> dict[str, Any]:
    """Read an ingress first-write row, with an injected-test compatibility fallback."""
    rows: list[dict[str, Any]] = []
    for owner in (getattr(ctx, "database", None), getattr(ctx, "memory_manager", None)):
        query = getattr(owner, "query_event_rows_by_ids", None)
        if callable(query):
            rows = list(query([event_id]))
            break
    if len(rows) == 1:
        return rows[0]
    if inserted:
        # Some narrow dependency-injection tests use persistence fakes that do
        # not expose a reread API. The just-inserted submitted payload is still
        # the first write; duplicate/repair paths never take this fallback.
        fallback = dict(submitted_event)
        fallback["id"] = event_id
        return fallback
    raise RuntimeError("durable ingress event could not be read back")


def _coerce_e2e_event_rows(rows: object, *, after_event_id: int = 0) -> list[dict[str, Any]]:
    if not isinstance(rows, list | tuple):
        return []
    coerced: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
        else:
            try:
                item = dict(row)
            except Exception:
                continue
        event_id = _event_row_id(item)
        if event_id is not None and event_id <= after_event_id:
            continue
        coerced.append(item)
    return sorted(coerced, key=lambda item: _event_row_id(item) or 0)


def _latest_e2e_event_id(ctx: Any) -> int:
    database = getattr(ctx, "database", None)
    conn = getattr(database, "conn", None)
    if conn is not None:
        try:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM events").fetchone()
            if row is not None:
                try:
                    return int(row["max_id"])
                except Exception:
                    return int(row[0])
        except Exception:
            pass

    memory_manager = getattr(ctx, "memory_manager", None)
    query_events = getattr(memory_manager, "query_events", None)
    if callable(query_events):
        try:
            rows = _coerce_e2e_event_rows(query_events(limit=1))
        except Exception:
            rows = []
        if rows:
            return _event_row_id(rows[-1]) or 0
    return 0


def _query_e2e_events(ctx: Any, *, after_event_id: int, limit: int = 1000) -> list[dict[str, Any]]:
    memory_manager = getattr(ctx, "memory_manager", None)
    query_events = getattr(memory_manager, "query_events", None)
    if callable(query_events):
        try:
            return _coerce_e2e_event_rows(
                query_events(after_event_id=after_event_id, limit=limit),
                after_event_id=after_event_id,
            )
        except TypeError:
            try:
                return _coerce_e2e_event_rows(
                    query_events(limit=limit),
                    after_event_id=after_event_id,
                )
            except Exception:
                return []
        except Exception:
            return []

    database = getattr(ctx, "database", None)
    query_events = getattr(database, "query_events", None)
    if callable(query_events):
        try:
            return _coerce_e2e_event_rows(
                query_events(after_event_id=after_event_id, limit=limit),
                after_event_id=after_event_id,
            )
        except Exception:
            return []
    return []


def _match_e2e_event(
    events: list[dict[str, Any]],
    *,
    platform: ExtensionE2EPlatform,
    action: ExtensionE2EAction,
    used_event_ids: set[int],
) -> dict[str, object] | None:
    accepted_event_types = _E2E_ACTION_EVENT_TYPES.get(action, frozenset())
    if not accepted_event_types:
        return None

    for row in sorted(events, key=lambda item: _event_row_id(item) or 0):
        event_id = _event_row_id(row)
        if event_id is None or event_id in used_event_ids:
            continue
        event_type = str(row.get("event_type") or row.get("type") or "").strip()
        if event_type not in accepted_event_types:
            continue
        metadata = _event_row_metadata(row)
        source_platform = _normalize_source_platform(
            metadata.get("source_platform")
            or row.get("source_platform")
            or _infer_source_platform_from_url(row.get("url", ""))
        )
        if source_platform != platform:
            continue
        used_event_ids.add(event_id)
        return {
            "event_id": event_id,
            "event_type": event_type,
            "url": str(row.get("url", "") or ""),
            "title": str(row.get("title", "") or ""),
        }
    return None


def _build_extension_e2e_report(
    state: _ExtensionE2ERunState,
    events: list[dict[str, Any]],
    *,
    timed_out: bool,
    timeout_seconds: int,
) -> ExtensionE2ERunOut:
    result = state.extension_result
    if state.native_save_authorization is not None:
        native_result = result.native_save_result if result is not None else None
        error = state.error
        if timed_out:
            native_run_status: ExtensionE2ERunStatus = "timeout"
            error = error or "extension e2e result timed out"
        elif native_result is None:
            native_run_status = "failed"
            error = error or "native save result missing"
        elif native_result.task_status in {"synced", "already_synced"}:
            native_run_status = "ok"
        elif native_result.task_status in {"pending", "syncing"}:
            native_run_status = "timeout"
            error = "native save task did not reach a terminal state"
        else:
            native_run_status = "failed"
            error = native_result.error_code or native_result.task_status
        return ExtensionE2ERunOut(
            run_id=state.run_id,
            status=native_run_status,
            error=error,
            timeout_seconds=timeout_seconds,
            native_save_result=native_result,
        )

    action_results: dict[
        tuple[ExtensionE2EPlatform, ExtensionE2EAction], tuple[ExtensionE2EActionStatus, str]
    ] = {}
    platform_details: dict[ExtensionE2EPlatform, str] = {}
    if result is not None:
        for platform_result in result.platforms:
            platform_details[platform_result.platform] = platform_result.detail
            for action_result in platform_result.actions:
                action_results[(platform_result.platform, action_result.action)] = (
                    action_result.status,
                    action_result.detail,
                )

    used_event_ids: set[int] = set()
    reports: list[ExtensionE2EPlatformReportOut] = []
    total_actions = 0
    complete_actions = 0
    partial_actions = 0
    default_status: ExtensionE2EActionStatus = "skipped" if timed_out else "failed"
    default_detail = "extension result timed out" if timed_out else "extension result missing"

    for platform, actions in state.expected_actions.items():
        action_reports: list[ExtensionE2EActionReportOut] = []
        for action in actions:
            total_actions += 1
            action_status, detail = action_results.get(
                (platform, action),
                (default_status, default_detail),
            )
            match = _match_e2e_event(
                events,
                platform=platform,
                action=action,
                used_event_ids=used_event_ids,
            )
            backend_event = (
                ExtensionE2EEventMatchOut(
                    event_id=cast("int", match["event_id"]),
                    event_type=str(match["event_type"]),
                    url=str(match["url"]),
                    title=str(match["title"]),
                )
                if match is not None
                else None
            )
            extension_executed = action_status == "ok"
            backend_matched = backend_event is not None
            if extension_executed and backend_matched:
                complete_actions += 1
            elif extension_executed or backend_matched:
                partial_actions += 1
            action_reports.append(
                ExtensionE2EActionReportOut(
                    action=action,
                    extension_status=action_status,
                    extension_executed=extension_executed,
                    extension_detail=detail,
                    backend_event_matched=backend_matched,
                    backend_event=backend_event,
                )
            )
        reports.append(
            ExtensionE2EPlatformReportOut(
                platform=platform,
                actions=action_reports,
                detail=platform_details.get(platform, ""),
            )
        )

    error = state.error or (result.error if result is not None else "")
    if timed_out:
        run_status: ExtensionE2ERunStatus = "timeout"
        error = error or "extension e2e result timed out"
    elif error and complete_actions == 0 and partial_actions == 0:
        run_status = "failed"
    elif total_actions == complete_actions:
        run_status = "ok"
    elif complete_actions > 0 or partial_actions > 0:
        run_status = "partial"
    else:
        run_status = "failed"

    return ExtensionE2ERunOut(
        run_id=state.run_id,
        status=run_status,
        platforms=reports,
        error=error,
        timeout_seconds=timeout_seconds,
    )


def _fallback_recommendation_click_url(
    *,
    source_platform: str,
    content_id: str,
    bvid: str,
) -> str:
    """Build a canonical click URL when the recommendation row lacks one."""
    item_id = (content_id or bvid).strip()
    if not item_id:
        return ""
    if source_platform == "youtube":
        return f"https://www.youtube.com/watch?v={quote(item_id, safe='')}"
    if source_platform == "douyin":
        return f"https://www.douyin.com/video/{quote(item_id, safe='')}"
    if source_platform == "twitter":
        return f"https://x.com/i/status/{quote(item_id, safe='')}"
    if source_platform == "reddit":
        reddit_id = item_id[3:] if item_id.startswith("t3_") else item_id
        return f"https://www.reddit.com/comments/{quote(reddit_id, safe='')}/"
    if source_platform == "bangumi":
        return f"https://bgm.tv/subject/{quote(item_id, safe='')}"
    if source_platform == "linuxdo":
        topic_id = item_id.removeprefix("topic:")
        return f"https://linux.do/t/{topic_id}" if topic_id.isdigit() and int(topic_id) > 0 else ""
    if source_platform == "v2ex":
        return f"https://www.v2ex.com/t/{quote(item_id, safe='')}"
    if source_platform == "bilibili":
        return f"https://www.bilibili.com/video/{quote(bvid or item_id, safe='')}"
    return ""


def _normalize_content_history_http_url(value: object) -> str:
    """Return an absolute HTTP(S) history URL or an empty safe value.

    Old Bilibili cache rows commonly store protocol-relative covers.  History
    is rendered by three clients, so normalize those once at the API boundary
    and never expose a script/data/file scheme (or a credential-bearing URL)
    for a client to navigate or render.
    """
    raw = str(value or "").strip()
    if raw.startswith("//"):
        raw = f"https:{raw}"
    if not raw or any(character.isspace() for character in raw):
        return ""
    if any(unicodedata.category(character).startswith("C") for character in raw):
        return ""
    try:
        parts = urlsplit(raw)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return ""
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.netloc
        or hostname is None
        or parts.username is not None
        or parts.password is not None
        or (port is not None and port <= 0)
    ):
        return ""
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc,
            parts.path,
            parts.query,
            parts.fragment,
        )
    )


def _encode_content_history_cursor(
    category: str,
    position: tuple[str, int, int, str, int, int, int],
) -> str:
    """Encode one stable history order tuple as a restart-safe opaque token."""
    payload = {
        "v": _CONTENT_HISTORY_CURSOR_VERSION,
        "category": category,
        "retention_days": CONTENT_HISTORY_RETENTION_DAYS,
        "after": list(position[:4]),
        "anchors": list(position[4:]),
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if len(token) > _CONTENT_HISTORY_CURSOR_MAX_LENGTH:  # pragma: no cover - trusted DB bound
        raise RuntimeError("content history cursor exceeds its encoded length bound")
    return token


def _decode_content_history_cursor(
    token: str,
    *,
    category: str,
) -> tuple[str, int, int, str, int, int, int]:
    """Strictly decode and bind an opaque history cursor to one category."""
    if (
        not token
        or len(token) > _CONTENT_HISTORY_CURSOR_MAX_LENGTH
        or re.fullmatch(r"[A-Za-z0-9_-]+", token) is None
    ):
        raise ValueError("invalid content history cursor encoding")
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.b64decode(
            (token + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid content history cursor encoding") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "v",
        "category",
        "retention_days",
        "after",
        "anchors",
    }:
        raise ValueError("invalid content history cursor payload")
    version = payload["v"]
    retention_days = payload["retention_days"]
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != _CONTENT_HISTORY_CURSOR_VERSION
        or payload["category"] != category
        or not isinstance(retention_days, int)
        or isinstance(retention_days, bool)
        or retention_days != CONTENT_HISTORY_RETENTION_DAYS
    ):
        raise ValueError("content history cursor does not match this request")
    after = payload["after"]
    if not isinstance(after, list) or len(after) != 4:
        raise ValueError("invalid content history cursor order tuple")
    occurred_at, source_kind, source_id, item_key = after
    if (
        not isinstance(occurred_at, str)
        or not occurred_at
        or len(occurred_at) > 64
        or any(unicodedata.category(character).startswith("C") for character in occurred_at)
    ):
        raise ValueError("invalid content history cursor timestamp")
    try:
        datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid content history cursor timestamp") from exc
    if (
        not isinstance(source_kind, int)
        or isinstance(source_kind, bool)
        or source_kind < 0
        or source_kind > 2
        or not isinstance(source_id, int)
        or isinstance(source_id, bool)
        or source_id < 0
        or source_id > _SQLITE_SIGNED_INTEGER_MAX
    ):
        raise ValueError("invalid content history cursor source position")
    if (
        not isinstance(item_key, str)
        or not item_key
        or len(item_key) > _CONTENT_HISTORY_CURSOR_ITEM_KEY_MAX_LENGTH
        or any(character.isspace() for character in item_key)
        or any(unicodedata.category(character).startswith("C") for character in item_key)
    ):
        raise ValueError("invalid content history cursor item key")
    anchors = payload["anchors"]
    if (
        not isinstance(anchors, list)
        or len(anchors) != 3
        or any(
            not isinstance(anchor, int)
            or isinstance(anchor, bool)
            or anchor < 0
            or anchor > _SQLITE_SIGNED_INTEGER_MAX
            for anchor in anchors
        )
    ):
        raise ValueError("invalid content history cursor snapshot anchors")
    event_anchor, recommendation_anchor, removal_anchor = anchors
    return (
        occurred_at,
        source_kind,
        source_id,
        item_key,
        event_anchor,
        recommendation_anchor,
        removal_anchor,
    )


def _normalize_recommendation_click_identity_url(value: str) -> str:
    """Normalize the URL used only when a click has no stable content ID."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return raw
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            parts.query,
            "",
        )
    )


def _normalize_probe_mode_for_payload(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _PROBE_MODES else "near"


def _probe_metadata_for_payload(item: object) -> tuple[str, bool]:
    probe_mode = _normalize_probe_mode_for_payload(getattr(item, "probe_mode", ""))
    challenge = probe_mode in _PROBE_CHALLENGE_MODES
    with suppress(Exception):
        challenge = challenge or bool(getattr(item, "challenge", False))
    return probe_mode, challenge


def _cap_keeping_user_added(
    items: list[Any], added: list[str], limit: int, key: Any = None
) -> list[Any]:
    """Truncate a merged AI⊕override list for the summary view without ever
    dropping a user-added entry.

    The effective profile appends user edits after the AI-inferred items, so a
    plain ``items[:limit]`` slice silently hides anything the user added past
    the cap — it then shows in edit mode (un-truncated `edit-state`) but not in
    the read-only view, which reads like "my edit didn't take". User edits are
    intentional and few, so they ride past the cap; only AI-inferred items are
    subject to it. ``key`` extracts the comparable string (identity for plain
    string lists, ``lambda d: d.domain`` for interest domains).
    """
    keyfn = key if key is not None else (lambda x: str(x))
    items = list(items)
    if len(items) <= limit:
        return items
    added_keys = {str(a).strip().casefold() for a in added if str(a).strip()}
    if not added_keys:
        return items[:limit]
    head = items[:limit]
    seen = {str(keyfn(x)).strip().casefold() for x in head}
    extra = [
        x
        for x in items[limit:]
        if str(keyfn(x)).strip().casefold() in added_keys
        and str(keyfn(x)).strip().casefold() not in seen
    ]
    return head + extra


def _cap_by_franchise(
    rows: list[dict[str, Any]],
    *,
    max_per_franchise: int = 2,
) -> list[dict[str, Any]]:
    """Drop later duplicates of the same ``franchise_key`` from a list.

    ``franchise_key`` is the LLM-tagged IP / series column (set during
    content evaluation, see ``llm/prompts.py`` and
    ``discovery/engine.py``). Empty franchise = general-interest content
    (科普 / 美食 / 通用资讯…) and passes through with no constraint —
    only matched IPs are subject to the cap.

    Why not in SQL: the recommendation pipeline orders by
    ``created_at DESC`` and we want a stable preserve-newest-N filter
    that's clearly testable. SQL window functions could do it, but the
    in-Python pass is cheap (≤ 40 rows) and easy to audit.
    """
    if max_per_franchise <= 0:
        return list(rows)
    seen: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        franchise = str(row.get("franchise_key", "") or "").strip()
        if not franchise:
            out.append(row)
            continue
        if seen.get(franchise, 0) >= max_per_franchise:
            continue
        seen[franchise] = seen.get(franchise, 0) + 1
        out.append(row)
    return out


def _normalize_cognition_update(item: dict[str, object]) -> CognitionUpdateSummary:
    impact = str(item.get("impact", "")).strip()
    reasoning = str(item.get("reasoning", "")).strip()
    evidence = str(item.get("evidence", "")).strip()
    source = str(item.get("source", "")).strip()
    source_label = str(item.get("source_label", "")).strip() or SOURCE_LABELS.get(source, "")
    expand_hint = str(item.get("expand_hint", "")).strip()
    if expand_hint not in {"expandable", "summary_only"}:
        expand_hint = "expandable" if any((impact, reasoning, evidence)) else "summary_only"
    return CognitionUpdateSummary(
        summary=str(item.get("summary", "")).strip(),
        context_line=str(item.get("context_line", "")).strip() or "基于最近几条相关内容",
        impact=impact,
        reasoning=reasoning,
        evidence=evidence,
        source=source,
        source_label=source_label,
        expand_hint=expand_hint,
        created_at=str(item.get("created_at", "")).strip(),
    )


def _derive_keyword_generation_mode(
    enabled: bool, replace: bool
) -> Literal["legacy", "hybrid", "inspiration"]:
    """Derive the UI-facing keyword-generation mode from the two canonical
    ``DiscoveryConfig`` booleans (``inspiration_search_enabled`` /
    ``inspiration_replace_merged_keywords``).

    Read-tolerant: ``enabled=False`` → ``"legacy"`` regardless of ``replace``
    (an off inspiration switch is legacy no matter what the stale replace flag
    says). ``enabled=True`` → ``"inspiration"`` when ``replace`` else
    ``"hybrid"``. This is a derived convenience for the UI/API only; the two
    booleans remain the single source of truth in ``config.toml``.
    """
    if not enabled:
        return "legacy"
    return "inspiration" if replace else "hybrid"


def _mode_to_flags(mode: str) -> tuple[bool, bool]:
    """Translate a UI keyword-generation mode into the canonical
    ``(inspiration_search_enabled, inspiration_replace_merged_keywords)``
    booleans. Every mode writes BOTH booleans so no stale ``replace`` residue
    survives a mode change: ``legacy → (False, False)``, ``hybrid → (True,
    False)``, ``inspiration → (True, True)``. ``mode`` must already be a valid
    Literal (the handler validates before calling).
    """
    return (mode != "legacy", mode == "inspiration")


def _mask_proxy_userinfo(url: str) -> str:
    """Mask any ``user:pass@`` credential in a proxy URL for GET responses.

    ``socks5://u:p@host:1`` → ``socks5://***@host:1``. A bare
    ``socks5://host:1`` has no secret and is returned verbatim.
    """
    if not url:
        return url
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment))


def _is_masked_proxy_echo(value: str) -> bool:
    """Whether a submitted proxy value is a masked GET echo (contains ``***``)."""
    return "***" in value


def create_app(
    *,
    memory_manager: Any | None = None,
    database: Any | None = None,
    soul_engine: Any | None = None,
    dialogue: Any | None = None,
    runtime_controller: Any | None = None,
    recommendation_engine: Any | None = None,
    runtime_event_hub: Any | None = None,
    account_sync_service: Any | None = None,
    auto_update_service: Any | None = None,
    project_stats_service: Any | None = None,
) -> FastAPI:
    """Create the local backend API app."""
    from openbiliclaw.api.runtime_context import (
        RuntimeContext,
        build_degraded_runtime_context,
        build_runtime_context,
    )
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.registry import RegistryBuildError

    app = FastAPI(title="OpenBiliClaw API", default_response_class=JSONResponse)

    # GZip middleware: only compress responses ≥ 500 bytes.
    # ``minimum_size=0`` was previously used as a sledgehammer workaround
    # for an h11 Content-Length mismatch on CJK text in older starlette
    # versions, but the side-effect was that 204/empty responses were
    # also force-compressed (gzip header alone is ~20 bytes > original
    # body), tripping h11's strict size check on every poll. Modern
    # starlette already encodes JSON bodies as UTF-8 bytes for
    # Content-Length, so the original workaround is no longer needed.
    from starlette.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Build RuntimeContext ────────────────────────────────────────
    config = load_config()

    # Mirror the overseas-outbound proxy into the process-level source of truth
    # before any LLM/updater client is built. CN-direct clients never read it.
    # getattr-guarded so a partial config object never hard-crashes app boot.
    #
    # The guard falls back to ``system``, not ``direct``. A config object missing
    # ``network`` / ``mode`` is the in-memory analogue of an *absent* key, not of
    # an invalid one: there is no user-written value here to disrespect, so it
    # takes the same default an absent key takes in ``_build_network_config``.
    # (``direct`` is reserved for present-but-broken values, where the user did
    # write something and silently inheriting an env proxy would override it.)
    # ``direct`` is also not the neutral choice — it actively sets
    # ``trust_env=False`` and overrides the user's environment, whereas ``system``
    # defers to it. When the config is too damaged to state a preference,
    # deferring beats overriding, and matching the normal default keeps an
    # already-degraded boot from growing a second, invisible failure mode
    # (opaque overseas timeouts on a machine that has a working proxy).
    from openbiliclaw.network import set_outbound_proxy

    network_config = getattr(config, "network", None)
    set_outbound_proxy(
        getattr(network_config, "proxy", "") or "",
        mode=getattr(network_config, "mode", "system") or "system",
    )

    if project_stats_service is None:
        from openbiliclaw.runtime.github_stars import GitHubStarCountService

        project_stats_service = GitHubStarCountService(
            cache_path=config.data_path / "cache" / "github-stars.json",
        )

    # Topic-lifecycle serialization switch (spec Phase 4). Off by default keeps
    # the LLM-facing profile byte-identical; on excludes archived topics.
    from openbiliclaw.discovery.strategies._utils import set_topic_lifecycle_serialization

    set_topic_lifecycle_serialization(
        str(getattr(getattr(config, "soul", None), "topic_lifecycle_serialization", "off"))
        .strip()
        .lower()
        == "on"
    )

    # Auto-generate the session signing secret on first enable so login state
    # survives restarts (see docs/plans/2026-05-30-web-password-auth-design.md).
    from openbiliclaw.api.auth import (
        AuthGate,
        _auth_env_overrides,
        authorize_websocket,
        ensure_session_secret,
        make_auth_middleware,
        reconcile_password_fingerprint,
        register_auth_routes,
        websocket_session_token,
    )
    from openbiliclaw.config import ApiAuthConfig as _ApiAuthConfig

    # Injection-path test doubles may hand back a config without ``api.auth``;
    # fall back to a disabled gate so the password feature stays inert there.
    _auth_cfg = getattr(getattr(config, "api", None), "auth", None)
    if not isinstance(_auth_cfg, _ApiAuthConfig):
        _auth_cfg = _ApiAuthConfig()

    if ensure_session_secret(_auth_cfg):
        with suppress(Exception):
            from openbiliclaw.config import save_config

            save_config(config)

    if soul_engine is not None:
        # Injection path: caller provides swappable components.
        # Auto-create stable components (database, memory_manager) if missing.
        from openbiliclaw.config import llm_concurrency_from_config
        from openbiliclaw.llm.concurrency import LLMConcurrencyGate
        from openbiliclaw.runtime.events import RuntimeEventHub as _RuntimeEventHub

        _db = database
        _created_db = False
        if _db is None:
            from openbiliclaw.storage.database import Database

            _db = Database(config.data_path / "openbiliclaw.db")
            _db.initialize()
            _created_db = True
        _mm = memory_manager
        if _mm is None:
            from openbiliclaw.memory.manager import MemoryManager

            _mm = MemoryManager(config.data_path, database=_db if _created_db else None)
            _mm.initialize()

        soul_service = getattr(soul_engine, "_llm_service", None)
        soul_declared_gate = getattr(soul_engine, "_llm_concurrency_gate", None)
        soul_service_gate = getattr(soul_service, "concurrency_gate", None)
        dialogue_service = getattr(dialogue, "_llm_service", None)
        dialogue_declared_gate = getattr(dialogue, "_llm_concurrency_gate", None) or getattr(
            dialogue, "llm_concurrency_gate", None
        )
        dialogue_service_gate = getattr(dialogue_service, "concurrency_gate", None)
        controller_gate = getattr(runtime_controller, "llm_concurrency_gate", None)
        recommendation_llm = getattr(recommendation_engine, "_llm", None)
        recommendation_gate = getattr(recommendation_llm, "concurrency_gate", None)
        account_soul = getattr(account_sync_service, "soul_engine", None)
        account_soul_service = getattr(account_soul, "_llm_service", None)
        controller_soul = getattr(runtime_controller, "soul_engine", None)
        controller_soul_service = getattr(controller_soul, "_llm_service", None)
        controller_recommendation = getattr(runtime_controller, "recommendation_engine", None)
        controller_recommendation_llm = getattr(controller_recommendation, "_llm", None)
        controller_discovery = getattr(runtime_controller, "discovery_engine", None)
        controller_discovery_llm = getattr(controller_discovery, "_llm_service", None)
        gate_sources = [
            ("SoulEngine", soul_declared_gate),
            ("SoulEngine service", soul_service_gate),
            ("dialogue", dialogue_declared_gate),
            ("dialogue service", dialogue_service_gate),
            ("runtime controller", controller_gate),
            ("recommendation service", recommendation_gate),
            ("account-sync SoulEngine", getattr(account_soul, "_llm_concurrency_gate", None)),
            (
                "account-sync SoulEngine service",
                getattr(account_soul_service, "concurrency_gate", None),
            ),
            (
                "runtime-controller SoulEngine",
                getattr(controller_soul, "_llm_concurrency_gate", None),
            ),
            (
                "runtime-controller SoulEngine service",
                getattr(controller_soul_service, "concurrency_gate", None),
            ),
            (
                "runtime-controller recommendation service",
                getattr(controller_recommendation_llm, "concurrency_gate", None),
            ),
            (
                "runtime-controller discovery service",
                getattr(controller_discovery_llm, "concurrency_gate", None),
            ),
        ]
        provided_gates = [(label, gate) for label, gate in gate_sources if gate is not None]
        injected_gate = provided_gates[0][1] if provided_gates else None
        conflicting_labels = [label for label, gate in provided_gates if gate is not injected_gate]
        if conflicting_labels:
            sources = ", ".join([provided_gates[0][0], *conflicting_labels])
            raise ValueError(
                f"Injected LLM-bearing components use different LLM concurrency gates: {sources}."
            )
        if injected_gate is None:
            injected_gate = LLMConcurrencyGate(llm_concurrency_from_config(config))

        with suppress(Exception):
            soul_engine._llm_concurrency_gate = injected_gate
        if soul_service is not None:
            with suppress(Exception):
                soul_service.concurrency_gate = injected_gate
        if dialogue is not None:
            with suppress(Exception):
                dialogue._llm_concurrency_gate = injected_gate
        if dialogue_service is not None:
            with suppress(Exception):
                dialogue_service.concurrency_gate = injected_gate
        if runtime_controller is not None:
            with suppress(Exception):
                runtime_controller.llm_concurrency_gate = injected_gate
        if recommendation_llm is not None:
            with suppress(Exception):
                recommendation_llm.concurrency_gate = injected_gate
        for nested_soul, nested_service in (
            (account_soul, account_soul_service),
            (controller_soul, controller_soul_service),
        ):
            if nested_soul is not None:
                with suppress(Exception):
                    nested_soul._llm_concurrency_gate = injected_gate
            if nested_service is not None:
                with suppress(Exception):
                    nested_service.concurrency_gate = injected_gate
        for nested_service in (
            controller_recommendation_llm,
            controller_discovery_llm,
        ):
            if nested_service is not None:
                with suppress(Exception):
                    nested_service.concurrency_gate = injected_gate

        ctx = RuntimeContext(
            database=_db,
            memory_manager=_mm,
            event_hub=runtime_event_hub
            or getattr(runtime_controller, "event_hub", None)
            or _RuntimeEventHub(),
            # config intentionally left None in injection path — matches
            # old behaviour where closures couldn't see config when all
            # core components were provided by the caller.
            soul_engine=soul_engine,
            dialogue=dialogue,
            dialogue_settlement_queue=getattr(dialogue, "_settlement_queue", None),
            runtime_controller=runtime_controller,
            recommendation_engine=recommendation_engine,
            account_sync_service=account_sync_service,
            auto_update_service=auto_update_service,
            llm_concurrency_gate=injected_gate,
        )
        if ctx.dialogue_settlement_queue is None:
            from openbiliclaw.api.runtime_context import (
                _build_dialogue_settlement_dispatcher,
            )
            from openbiliclaw.soul.dialogue_learn_queue import DialogueSettlementQueue

            anchor_manager = getattr(soul_engine, "_dialogue_anchor_manager", None)
            anchor_provider = getattr(anchor_manager, "snapshot", None)
            ctx.dialogue_settlement_queue = DialogueSettlementQueue(
                _build_dialogue_settlement_dispatcher(
                    soul_engine,
                    ctx.dialogue_settlement_handlers,
                ),
                anchor_provider=anchor_provider if callable(anchor_provider) else None,
                guard=ctx.dialogue_settlement_guard,
            )
            bind_settlement_queue = getattr(
                soul_engine,
                "bind_dialogue_settlement_queue",
                None,
            )
            if callable(bind_settlement_queue):
                bind_settlement_queue(ctx.dialogue_settlement_queue)
        if ctx.dialogue is None:
            from openbiliclaw.soul.dialogue import (
                DialogueLearningMode,
                SocraticDialogue,
            )

            ctx.dialogue = SocraticDialogue(
                llm=None,
                soul_engine=soul_engine,
                session="popup",
                learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
            )
        if ctx.auto_update_service is None:
            from openbiliclaw.runtime.updater import AutoUpdateService

            ctx.auto_update_service = AutoUpdateService(
                enabled=False,
                event_publisher=getattr(ctx.event_hub, "publish", None),
            )
    else:
        # Production path: build everything from config.
        try:
            ctx = build_runtime_context(
                config,
                memory_manager=memory_manager,
                database=database,
                event_hub=runtime_event_hub,
            )
        except RegistryBuildError as exc:
            ctx = build_degraded_runtime_context(
                config,
                memory_manager=memory_manager,
                database=database,
                event_hub=runtime_event_hub,
                exc=exc,
            )
            logger.warning(
                "FastAPI started in degraded mode (%s): %s",
                ctx.degraded_reason,
                "; ".join(str(getattr(issue, "message", issue)) for issue in ctx.degraded_issues),
            )
    if ctx.llm_concurrency_gate is None:
        from openbiliclaw.config import llm_concurrency_from_config
        from openbiliclaw.llm.concurrency import LLMConcurrencyGate

        ctx.llm_concurrency_gate = LLMConcurrencyGate(llm_concurrency_from_config(config))

    # Fan freshly recorded LLM/embedding anomaly alerts out to the live
    # runtime stream so the 异常报警 feed updates without a poll round-trip.
    with suppress(Exception):
        get_diagnostics_alert_buffer().set_publisher(getattr(ctx.event_hub, "publish", None))

    # The process-lifetime migration guard was acquired for the data directory
    # that was active at startup.  A newly persisted ``data_dir`` must therefore
    # remain restart-only: every live data read/write and every hot rebuild is
    # pinned to this immutable path until a fresh process acquires the new lock.
    # Capture the effective startup config, which is the path the supervisor
    # locked before constructing this app. Injected tests may deliberately pass
    # a standalone Database or MemoryManager rooted elsewhere; those component
    # fixtures do not redefine where source credentials and other project data
    # live. Runtime config is therefore the sole authority for this process-wide
    # path, just as it is for the startup guard.
    active_runtime_data_path = Path(config.data_path).expanduser().resolve()
    app.state.active_runtime_data_path = active_runtime_data_path

    def _active_runtime_data_path() -> Path:
        """Return the canonical data directory protected by this process's lock."""
        return active_runtime_data_path

    def _pin_active_runtime_config(candidate: Any) -> Any:
        """Copy *candidate* while retaining this process's locked data directory."""
        pinned = copy.deepcopy(candidate)
        if not hasattr(pinned, "data_dir") and not hasattr(pinned, "data_path"):
            # Narrow injected test doubles may use an opaque sentinel for the
            # candidate. Production rebuilds always receive Config.
            return pinned
        pinned.data_dir = str(active_runtime_data_path)
        return pinned

    def _preserve_persisted_restart_fields(candidate: Any) -> Any:
        """Merge restart-only values from disk before a whole-config write."""
        desired = load_config(consult_environment=False)
        merged = copy.deepcopy(candidate)
        merged.data_dir = str(desired.data_dir)
        return merged

    def _inventory_target() -> int:
        controller_target = getattr(ctx.runtime_controller, "pool_target_count", None)
        if controller_target is not None:
            with suppress(TypeError, ValueError):
                return max(0, int(controller_target))
        runtime_scheduler = getattr(getattr(ctx, "config", None), "scheduler", None)
        configured_target = getattr(runtime_scheduler, "pool_target_count", None)
        if configured_target is None:
            configured_target = getattr(
                getattr(config, "scheduler", None),
                "pool_target_count",
                0,
            )
        with suppress(TypeError, ValueError):
            return max(0, int(cast("Any", configured_target)))
        return 0

    def _xhs_self_nickname() -> str:
        """Persisted xhs nickname used by the pool's self-authored guard."""
        load_state = getattr(ctx.memory_manager, "load_discovery_runtime_state", None)
        if callable(load_state):
            with suppress(Exception):
                state = load_state()
                info = state.get("xhs_self_info", {}) if isinstance(state, dict) else {}
                if isinstance(info, dict):
                    return str(info.get("nickname", "") or "").strip()
        return ""

    def _canonical_pool_available() -> int | None:
        nickname = _xhs_self_nickname()
        readiness = getattr(ctx.database, "count_pool_readiness", None)
        if callable(readiness):
            with suppress(Exception):
                counts = readiness(xhs_self_nickname=nickname)
                if isinstance(counts, dict):
                    return max(0, int(counts.get("available", 0)))
        count_pool = getattr(ctx.database, "count_pool_candidates", None)
        if callable(count_pool):
            with suppress(TypeError):
                return max(0, int(count_pool(xhs_self_nickname=nickname)))
            with suppress(Exception):
                return max(0, int(count_pool()))
        return None

    initial_available = _canonical_pool_available()
    update_inventory = getattr(ctx.llm_concurrency_gate, "update_inventory", None)
    if initial_available is not None and callable(update_inventory):
        update_inventory(available=initial_available, target=_inventory_target())
    app.state.runtime_context = ctx
    auto_replenishment_task: asyncio.Task[None] | None = None
    auto_replenishment_started_at = 0.0
    first_page_topup_attempted_at = 0.0
    recommendation_snapshot_cache: RecommendationListResponse | None = None
    recommendation_snapshot_cached_at = 0.0
    recommendation_snapshot_expires_at = 0.0
    recommendation_snapshot_dislike_digest = ""
    recommendation_snapshot_lock = asyncio.Lock()

    def _invalidate_recommendation_snapshot() -> None:
        nonlocal recommendation_snapshot_cache, recommendation_snapshot_cached_at
        nonlocal recommendation_snapshot_expires_at
        nonlocal recommendation_snapshot_dislike_digest
        recommendation_snapshot_cache = None
        recommendation_snapshot_cached_at = 0.0
        recommendation_snapshot_expires_at = 0.0
        recommendation_snapshot_dislike_digest = ""

    def _effective_recommendation_dislikes() -> tuple[list[str], str]:
        """Read the latest output-policy snapshot without waiting for rebuild."""

        from openbiliclaw.recommendation.exclusion import disliked_topics_digest

        getter = getattr(ctx.soul_engine, "get_effective_disliked_topics", None)
        topics: list[str] = []
        if callable(getter):
            topics = [str(item).strip() for item in getter() if str(item).strip()]
        return topics, disliked_topics_digest(topics)

    app.state.degraded = bool(getattr(ctx, "degraded", False))
    app.state.degraded_reason = str(getattr(ctx, "degraded_reason", ""))
    app.state.degraded_issues = list(getattr(ctx, "degraded_issues", []))
    feedback_batch_scheduler = FeedbackBatchScheduler(
        soul_engine_resolver=lambda: getattr(ctx, "soul_engine", None),
        debounce_seconds=_FEEDBACK_BATCH_DEBOUNCE_SECONDS,
    )
    app.state.feedback_batch_scheduler = feedback_batch_scheduler
    app.state.event_recovery_task = None
    dialogue_execution_coordinator = DialogueExecutionCoordinator()
    app.state.dialogue_execution_coordinator = dialogue_execution_coordinator
    config_runtime_reload_lock = asyncio.Lock()
    config_apply_task: asyncio.Task[None] | None = None
    config_apply_pending: _QueuedConfigApply | None = None
    config_apply_revision = 0
    config_applied_revision = 0
    config_apply_state: Literal["idle", "queued", "applying", "applied", "failed"] = "idle"
    config_apply_message = ""
    config_apply_error = ""
    config_apply_updated_at = ""
    config_last_good = _pin_active_runtime_config(getattr(ctx, "config", None) or config)
    app.state.config_apply_task = None
    app.state.migration_import_request_id = ""
    app.state.migration_import_phase = ""
    image_fetch_coordinator = ImageFetchCoordinator()
    app.state.image_fetch_coordinator = image_fetch_coordinator
    chat_reply_scheduler: DurableChatReplyScheduler

    def _complete_degraded_runtime_recovery() -> None:
        """Publish an atomically rebuilt degraded context as fully available.

        ``RuntimeContext.rebuild_from_config`` constructs every swappable
        component before publishing any of them, so once it returns the process
        no longer needs the startup-only degraded guard.  Keep both the
        authoritative context and the legacy ``app.state`` mirrors in sync, and
        rebind the one API-owned scheduler that captured ``soul_engine`` during
        app construction.
        """
        ctx.degraded = False
        ctx.degraded_reason = ""
        ctx.degraded_issues = []
        app.state.degraded = False
        app.state.degraded_reason = ""
        app.state.degraded_issues = []
        feedback_batch_scheduler.soul_engine = ctx.soul_engine

    # ── Password gate (LAN/remote auth) ─────────────────────────────
    app.state.auth_gate = AuthGate(_auth_cfg, getattr(ctx, "database", None))
    app.state.extension_e2e_runs = {}

    def _get_auth_gate() -> AuthGate:
        return cast("AuthGate", app.state.auth_gate)

    register_auth_routes(app, _get_auth_gate)

    @app.post("/api/auth/admin")
    async def auth_admin(request: Request) -> JSONResponse:
        """Local-only enable/disable + set/change of the password gate.

        Lives here (not in register_auth_routes) so it shares ``PUT /api/config``'s
        ``_CONFIG_SAVE_LOCK`` + snapshot/rollback — its full-file ``save_config``
        must not race with a concurrent settings save (review r1#3). Callable only
        by a trusted-local client (extension / local UI / CLI), never a remote
        session ("change the lock only from inside the house"); applied live (no
        restart); refused when env-managed.
        """
        import secrets as _secrets

        from openbiliclaw import auth_core as _ac
        from openbiliclaw.config import _default_config_path as _cfg_path
        from openbiliclaw.config import get_auth_plain_password as _get_plain
        from openbiliclaw.config import load_config as _load
        from openbiliclaw.config import save_config as _save

        gate = _get_auth_gate()
        if not gate.is_trusted_local(request):
            return JSONResponse({"ok": False, "error": "local_only"}, status_code=403)
        if gate.database is None:
            return JSONResponse({"ok": False, "error": "unavailable"}, status_code=503)
        env_vars = _auth_env_overrides()
        if env_vars:
            return JSONResponse(
                {"ok": False, "error": "env_managed", "vars": env_vars}, status_code=409
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        enabled = bool(body.get("enabled"))
        password = body.get("password")
        password = str(password) if password is not None else None
        ttl = body.get("session_ttl_hours")

        async with _CONFIG_SAVE_LOCK:
            cfg = _load()  # re-read inside the lock to avoid clobbering a concurrent save
            auth = cfg.api.auth
            was_enabled = auth.enabled
            if enabled:
                if password and password.strip():
                    auth.password_hash = _ac.hash_password(password)
                if not auth.password_hash.strip():
                    return JSONResponse(
                        {"ok": False, "error": "password_required"}, status_code=400
                    )
                auth.enabled = True
                if not auth.session_secret.strip():
                    auth.session_secret = _secrets.token_urlsafe(32)
                if ttl is not None:
                    with suppress(TypeError, ValueError):
                        auth.session_ttl_hours = max(0, int(ttl))
            else:
                auth.enabled = False

            # force_bump revokes on an enabled on/off toggle or an explicit
            # password in this request (neither is guaranteed to change the
            # fingerprint). A credential change the request can't see — e.g. a
            # password_hash that drifted on disk via an out-of-band `set-password`
            # while running — is caught by revoke_and_set_fingerprint comparing the
            # new fingerprint to the stored one inside its transaction (r4#2).
            force_bump = (auth.enabled != was_enabled) or bool(password and password.strip())
            config_path = _cfg_path()
            config_existed = config_path.exists()
            backup_path = _snapshot_config_file(config_path)

            def _rollback_cfg() -> None:
                # Restore config.toml to its pre-save state on any failure path. If
                # it existed, restore the snapshot; if it did NOT (backup is None),
                # remove anything _save created so a failed change leaves no durable
                # config behind (review r11#2).
                if backup_path is not None:
                    with suppress(Exception):
                        _restore_config_snapshot(backup_path, config_path)
                elif not config_existed:
                    with suppress(Exception):
                        config_path.unlink(missing_ok=True)

            # 1) Persist to disk FIRST (snapshot + rollback, like PUT /api/config).
            #    Nothing is published to the live gate or the DB yet, so a write
            #    failure here leaves ALL durable + live state on the old password.
            try:
                _save(cfg)
            except Exception:
                _rollback_cfg()
                logger.warning("auth: admin save_config failed", exc_info=True)
                return JSONResponse({"ok": False, "error": "unavailable"}, status_code=503)
            # 2) Verify the write is EFFECTIVE as startup will see it. config.toml is
            #    not the only layer: load_config merges config.local.toml OVER it
            #    (local wins). If config.local pins an auth field, our config.toml
            #    write silently reverts on restart while the live gate briefly shows
            #    success. Reload the merged effective config; if the intended change
            #    didn't take, roll back and report a conflict instead of a false
            #    success (review r9). (env is refused earlier with 409.)
            effective = _load().api.auth
            shadowed = effective.enabled != cfg.api.auth.enabled
            if enabled and password and password.strip():
                shadowed = shadowed or not _ac.verify_password(password, effective.password_hash)
            if enabled and ttl is not None:
                shadowed = shadowed or effective.session_ttl_hours != cfg.api.auth.session_ttl_hours
            if shadowed:
                _rollback_cfg()
                logger.warning("auth: admin change shadowed by config.local.toml; not applied")
                return JSONResponse({"ok": False, "error": "shadowed"}, status_code=409)
            # 3) Derive the fingerprint from the SAME material the startup reconcile
            #    will read AFTER this save — get_auth_plain_password() on the JUST-
            #    persisted file (env is refused above). save_config may keep an
            #    unchanged plaintext `password` line (→ "pw:"+plain) or persist
            #    hash-only (→ "ph:"+hash); reading post-save makes our stored
            #    fingerprint match reconcile's exactly, so a successful change never
            #    spuriously revokes on the next restart (review r3#1 / r8).
            plain_after = _get_plain()
            fingerprint = (
                _ac.password_fingerprint(
                    auth.session_secret, plain=plain_after, password_hash=auth.password_hash
                )
                if (auth.password_hash.strip() and auth.session_secret.strip())
                else None
            )
            # 4) Durable revocation (atomic). If it fails, roll the config file back
            #    so the persisted password still matches the UNCHANGED DB
            #    fingerprint/epoch, and do NOT publish — old sessions stay valid
            #    under the old password (revoke-first would instead commit an epoch
            #    bump + fingerprint that the config rollback can't undo). A crash
            #    BETWEEN the steps is self-healed by reconcile_password_fingerprint
            #    at startup: config's new password vs the stale DB fingerprint
            #    mismatches → bump + store → the change completes deterministically.
            try:
                gate.database.revoke_and_set_fingerprint(fingerprint, force_bump=force_bump)
            except Exception:
                _rollback_cfg()
                logger.warning("auth: admin revoke failed; change not applied", exc_info=True)
                return JSONResponse({"ok": False, "error": "unavailable"}, status_code=503)
            # 5) Publish live so it takes effect without a restart.
            gate.auth = cfg.api.auth
            gate.reconcile_ok = True

        logger.info("auth: gate %s via local admin", "enabled" if enabled else "disabled")
        return JSONResponse(
            {
                "ok": True,
                "enabled": cfg.api.auth.enabled,
                "trust_loopback": cfg.api.auth.trust_loopback,
            }
        )

    with suppress(Exception):
        from openbiliclaw.config import get_auth_plain_password

        reconcile_password_fingerprint(app.state.auth_gate, plain=get_auth_plain_password())

    def _degraded_issues_payload() -> list[dict[str, str]]:
        return [
            {
                "field": str(getattr(issue, "field", "")),
                "message": str(getattr(issue, "message", issue)),
                "severity": str(getattr(issue, "severity", "warning")),
            }
            for issue in getattr(ctx, "degraded_issues", [])
        ]

    def _degraded_body() -> dict[str, object]:
        return {
            "status": "degraded",
            "reason": str(getattr(ctx, "degraded_reason", "")),
            "issues": _degraded_issues_payload(),
        }

    # Shared copy for the degraded-mode init rejection (POST /api/init) and the
    # degraded-aware /api/init-status reason detail, so both surfaces explain
    # the same actionable cause: the backend could not build its LLM registry,
    # so init is impossible until the LLM config is repaired (mirrors the
    # config-recovery message on PUT /api/config).
    _degraded_init_detail = (
        "LLM 配置有误，AI 服务无法启动，暂时无法初始化。"
        "请到设置页修正 LLM provider 配置（API key / 模型 / 接口地址）并保存；"
        "校验通过后端会原地恢复，无需重启。"
    )

    def _init_blocked_by_degraded() -> bool:
        """True when the backend is degraded because the LLM registry never
        built — the one degraded reason that makes guided init impossible.

        Gated on the canonical ``llm_registry_unavailable`` reason (the only
        value ``build_degraded_runtime_context`` ever emits) rather than the
        bare ``degraded`` flag, so this stays a precise "LLM config is broken"
        signal and does not swallow other, init-compatible degraded states.
        """
        return (
            bool(getattr(ctx, "degraded", False))
            and str(getattr(ctx, "degraded_reason", "")) == "llm_registry_unavailable"
        )

    @app.middleware("http")
    async def _degraded_mode_guard(request: Request, call_next: Any) -> Any:
        if not bool(getattr(ctx, "degraded", False)):
            return await call_next(request)
        path = request.url.path
        method = request.method.upper()
        static_recovery_surface = (
            path == "/"
            or path in {"/m", "/web", "/setup"}
            # /shared/ hosts modules the recovery shells load at parse time
            # (e.g. source-status.js); blocking it kills the setup wizard's
            # script and leaves the degraded config unrepairable.
            or path.startswith(("/m/", "/web/", "/setup/", "/shared/"))
        )
        allowed = (
            method == "OPTIONS"
            or path == "/api/ping"
            or path == "/api/qr-info"
            or path == "/api/project-stats"
            or path == "/api/health"
            or path == "/api/runtime-status"
            or (path == "/api/content-history" and method == "GET")
            or path == "/favicon.ico"
            or path == "/api/autostart-status"
            or path == "/api/autostart/apply"
            or path in ("/api/init-status", "/api/init", "/api/init/cancel")
            # Update status + manual check/apply: a backend that can't build its
            # LLM registry is exactly when pulling a fix-carrying release matters,
            # so the recovery surface must stay reachable while degraded.
            or path in ("/api/update-status", "/api/update/check", "/api/update/apply")
            or (path == "/api/config" and method in {"GET", "PUT"})
            or (path == "/api/config/apply-status" and method == "GET")
            or (
                path
                in {
                    "/api/migration/export",
                    "/api/migration/import",
                    "/api/migration/pending",
                    "/api/migration/status",
                }
                and method in {"DELETE", "GET", "POST"}
            )
            # Draft-only config helpers are part of the recovery control
            # plane, not business LLM traffic.  Each builds from the submitted
            # form (or config + DB for source shares), so blocking it here made
            # the setup wizard and both settings UIs unable to validate the
            # replacement for the registry that failed during startup.
            or (
                path
                in {
                    "/api/config/probe-service",
                    "/api/config/discover-models",
                }
                and method == "POST"
            )
            or (path == "/api/config/source-share-suggestion" and method in {"GET", "POST"})
            # LLM-independent repair/config surfaces (degraded ctx has config +
            # database, which is all these handlers touch). Blocking them made
            # the settings 平台源 tab and the embedding banner fail with
            # misleading "backend unavailable" copy while degraded, even though
            # fixing platform logins / pulling bge-m3 is exactly what a user
            # can usefully do while repairing the LLM config.
            or path == "/api/sources/status"
            or (path.startswith("/api/sources/") and path.endswith("/verify"))
            or path == "/api/embedding/repair"
            or path.startswith("/api/auth")
            # Keep every browser recovery shell loadable. Their static assets
            # do not depend on the LLM registry, and the desktop/setup forms
            # use the allowed config endpoints above to repair the blocking
            # provider configuration. Blocking these paths exposes the raw 503
            # envelope instead of the recovery UI.
            or static_recovery_surface
        )
        if allowed:
            return await call_next(request)
        return JSONResponse(status_code=503, content=_degraded_body())

    def _init_active_now() -> bool:
        """Defensive ``init_active`` check usable from any handler/middleware.

        Returns False (never raises) when the coordinator/DB is a test stub or
        unavailable, so gating logic degrades to "not active" instead of 500.
        """
        coord = getattr(ctx, "init_coordinator", None)
        if coord is None:
            return False
        try:
            return bool(coord.init_active())
        except Exception:
            return False

    def _init_owns_task(task_id: str) -> bool:
        """Whether ``task_id`` is a bootstrap task enqueued by the active init
        run (so its task-result is init's own data, not a stale/steady-state
        completion). Defensive — never raises."""
        coord = getattr(ctx, "init_coordinator", None)
        if coord is None or not task_id:
            return False
        try:
            return bool(coord.is_owned_bootstrap_task(str(task_id)))
        except Exception:
            return False

    def _init_owned_ids_filter() -> set[str] | None:
        """``next-task`` filter: during an active init, restrict the dispatcher
        to init-owned bootstrap task ids (so a stale pending task can't be
        claimed and starve the run's collectors); None = no restriction."""
        if not _init_active_now():
            return None
        coord = getattr(ctx, "init_coordinator", None)
        if coord is None:
            return None
        try:
            return set(coord.owned_task_ids())
        except Exception:
            return None

    def _cancel_disabled_source_incremental_tasks(source: str) -> None:
        """Keep periodic rows from being claimed after global or per-source opt-out."""

        source_config_attr = {
            "xhs": "xiaohongshu",
            "dy": "douyin",
            "yt": "youtube",
            "zhihu": "zhihu",
            "reddit": "reddit",
            "linuxdo": "linuxdo",
            "v2ex": "v2ex",
        }.get(source, source)
        scheduler_cfg = getattr(getattr(ctx, "config", None), "scheduler", None)
        sources_cfg = getattr(getattr(ctx, "config", None), "sources", None)
        source_cfg = (
            getattr(sources_cfg, source_config_attr, None) if sources_cfg is not None else None
        )
        source_disabled = source_cfg is not None and not bool(getattr(source_cfg, "enabled", False))
        if (
            bool(getattr(scheduler_cfg, "enabled", True))
            and bool(getattr(scheduler_cfg, "source_incremental_enabled", False))
            and not source_disabled
        ):
            return
        scheduler_task_ids: set[str] = set()
        try:
            load_state = getattr(ctx.memory_manager, "load_source_bootstrap_state", None)
            state = load_state() if callable(load_state) else {}
            raw_incremental = state.get("source_incremental", {})
            raw_active = (
                raw_incremental.get("active_task") if isinstance(raw_incremental, dict) else None
            )
            if isinstance(raw_active, dict):
                task_id = str(raw_active.get("task_id", "") or "").strip()
                if task_id:
                    scheduler_task_ids.add(task_id)
        except Exception:
            logger.warning("source incremental owner state read failed", exc_info=True)
        try:
            from openbiliclaw.sources.source_bootstrap import (
                cancel_incremental_bootstrap_tasks,
            )

            cancelled = cancel_incremental_bootstrap_tasks(
                ctx.database,
                sources={source},
                scheduler_task_ids=scheduler_task_ids,
            )
        except Exception:
            logger.warning(
                "disabled source incremental claim cleanup failed source=%s",
                source,
                exc_info=True,
            )
            return
        if cancelled:
            logger.info(
                "cancelled disabled source incremental tasks source=%s count=%d",
                source,
                len(cancelled),
            )

    # gui-init D1 — DENY-BY-DEFAULT writer gating. While a guided init is active,
    # every mutating request (POST/PUT/PATCH/DELETE) is rejected with 409 unless
    # it is on the small allowlist of init-essential writers below. An allowlist
    # of *blocked* paths is fragile (every new soul/pool writer must remember to
    # opt in); denying by default means no writer can silently race init.
    #
    # Allowed during init:
    #  - /api/init, /api/init/cancel        — init control itself
    #  - /api/config/probe-service          — read-only draft probes for LLM,
    #                                         embedding, and network settings;
    #                                         the LLM path still uses the stable
    #                                         total concurrency gate
    #  - /api/bilibili/cookie               — handler no-ops during init
    #  - /api/auth/*                        — auth-gate management (login/admin)
    #  - /api/sources/*/kick                — init's own dispatcher kick
    #  - /api/sources/*/task-result         — init bootstrap results (the handler
    #                                         self-guards: skips pool writes and
    #                                         only propagates init-owned results)
    #  - /api/sources/bangumi/identity       — extension-reported Bangumi account
    #                                         (public uid + username). Init is the
    #                                         moment three-tier account resolution
    #                                         (token > explicit username > extension
    #                                         identity) needs the freshly-reported
    #                                         identity, so it must land even mid-init.
    # (GET reads — /api/sources/*/next-task, /api/init-status, … — are never
    #  gated since only mutating methods are checked.)
    _init_write_allowlist = frozenset(
        {
            "/api/init",
            "/api/init/cancel",
            "/api/config/probe-service",
            "/api/bilibili/cookie",
            "/api/migration/pending",
            "/api/sources/bangumi/identity",
            "/api/sources/linuxdo/login-state",
            "/api/sources/linuxdo/credential",
            "/api/sources/v2ex/identity",
            "/api/sources/v2ex/login-state",
            "/api/sources/v2ex/credential",
            "/api/sources/weibo/login-state",
            "/api/sources/weibo/credential",
        }
    )

    def _init_write_allowed(path: str) -> bool:
        if path in _init_write_allowlist or path.startswith("/api/auth"):
            return True
        # Exact-segment match for the bootstrap protocol: only
        # /api/sources/<source>/{kick,task-result}. Split WITHOUT stripping so a
        # trailing slash ("/api/sources/xhs/kick/") yields 6 parts and is NOT
        # allowed, and recipe CRUD like /api/sources/kick (recipe_id="kick")
        # yields 4 parts and is NOT allowed.
        segments = path.split("/")  # "/api/sources/xhs/kick" → ['', api, sources, xhs, kick]
        return (
            len(segments) == 5
            and segments[1] == "api"
            and segments[2] == "sources"
            and (segments[4] in ("kick", "task-result"))
        )

    @app.middleware("http")
    async def _init_active_write_guard(request: Request, call_next: Any) -> Any:
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            path = request.url.path
            if not _init_write_allowed(path) and _init_active_now():
                return JSONResponse(
                    {"error": "init_running", "detail": "初始化进行中，请稍后再试"},
                    status_code=409,
                )
        return await call_next(request)

    # Register AFTER the degraded guard so the auth gate is the outermost http
    # middleware (runs first): unauthenticated requests are rejected before any
    # downstream handling. CORS stays inner; 401/403 echo a permissive header.
    app.middleware("http")(make_auth_middleware(_get_auth_gate))

    def _schedule_post_feedback_tasks() -> None:
        with suppress(Exception):
            feedback_batch_scheduler.schedule()

    def _unified_feedback_owner_enabled() -> bool:
        return bool(
            ctx.soul_engine is not None
            and getattr(ctx.soul_engine, "unified_interest_line_enabled", False)
        )

    def _feedback_owner_storage_available(soul_engine: Any) -> bool:
        """Whether this engine has the durable state contract its owner needs.

        RuntimeContext keeps injected stable components across config reloads.
        A degraded embedder may therefore rebuild a real ``SoulEngine`` around
        a deliberately narrow memory adapter.  Method presence is the honest
        capability boundary: skip owner publication when that adapter cannot
        persist/read the cursor, while allowing real I/O failures from a
        complete adapter to propagate and abort the handoff.
        """
        memory = getattr(soul_engine, "_memory", None)
        if memory is None:
            memory = getattr(ctx, "memory_manager", None)
        required = (
            "load_feedback_state",
            "save_feedback_state",
            "query_events_since",
            "get_latest_event_id",
        )
        return all(callable(getattr(memory, method, None)) for method in required)

    async def _prepare_unified_feedback_owner() -> None:
        """Publish the v1→v2 owner fence before any new feedback event write."""
        if not _unified_feedback_owner_enabled():
            return
        soul_engine = getattr(ctx, "soul_engine", None)
        if not _feedback_owner_storage_available(soul_engine):
            return
        prepare = getattr(soul_engine, "prepare_feedback_owner_cutover", None)
        if callable(prepare):
            await prepare()

    async def _prepare_event_owners() -> None:
        """Fence current hot-reload runtime owners before event-only commits."""
        soul_engine = getattr(ctx, "soul_engine", None)
        prepare_profile = getattr(soul_engine, "prepare_profile_event_owner_cutover", None)
        if callable(prepare_profile):
            await prepare_profile()
        if _unified_feedback_owner_enabled() and _feedback_owner_storage_available(soul_engine):
            prepare_feedback = getattr(soul_engine, "prepare_feedback_owner_cutover", None)
            if callable(prepare_feedback):
                await prepare_feedback()

    event_ingress = EventIngressService(
        getattr(ctx, "memory_manager", None),
        memory_manager_resolver=lambda: getattr(ctx, "memory_manager", None),
        prepare_owner=_prepare_event_owners,
        wake=_schedule_post_feedback_tasks,
    )
    app.state.event_ingress = event_ingress

    def _bind_runtime_lane_dependencies() -> None:
        runtime_controller = getattr(ctx, "runtime_controller", None)
        if runtime_controller is not None:
            try:
                runtime_controller.image_fetch_coordinator = image_fetch_coordinator
            except (AttributeError, TypeError):
                logger.debug("runtime fixture does not accept image fetch binding")
        account_sync = getattr(ctx, "account_sync_service", None)
        if account_sync is not None:
            try:
                account_sync.event_ingress = event_ingress
            except (AttributeError, TypeError):
                # Narrow dependency-injection fixtures may use immutable
                # sentinels; production AccountSyncService exposes this slot.
                logger.debug("account sync fixture does not accept event ingress binding")

    _bind_runtime_lane_dependencies()

    async def _accept_source_profile_events(
        *,
        source: str,
        task_id: str,
        events_with_keys: list[tuple[dict[str, Any], str]],
        generic_owner: bool,
    ) -> list[str]:
        """Persist extension source results through the durable ingress.

        Guided init owns profile construction, so its events are durable facts
        but deliberately carry no incremental owner marker. Steady-state source
        imports opt into the generic cursor explicitly.
        """
        canonical: list[dict[str, Any]] = []
        keys_by_index: list[str] = []
        for raw_event, bootstrap_key in events_with_keys:
            event = dict(raw_event)
            metadata = event.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, dict) else {}
            metadata.setdefault("event_namespace", "source_import")
            metadata["source_task_id"] = task_id
            if generic_owner:
                metadata["profile_update_owner"] = "generic"
            else:
                metadata.pop("profile_update_owner", None)
            event["metadata"] = metadata
            stable_content_id = str(
                metadata.get("content_id")
                or metadata.get("bvid")
                or metadata.get("note_id")
                or metadata.get("aweme_id")
                or metadata.get("creator_sec_uid")
                or metadata.get("video_id")
                or metadata.get("channel_id")
                or ""
            ).strip()
            stable_item_id = bootstrap_key.strip() or stable_content_id
            if stable_item_id:
                item_identity = f"id:{stable_item_id}"
            else:
                # Signed source URLs are mutable transport locators. They are
                # only an identity fallback when the adapter supplied no
                # stable bootstrap/content key, and fragments are irrelevant.
                normalized_url = _normalize_recommendation_click_identity_url(
                    str(event.get("url") or "")
                )
                item_identity = f"url:{normalized_url}"
            stable_identity = "|".join(
                (
                    source,
                    item_identity,
                    str(event.get("event_type") or event.get("type") or ""),
                    str(
                        metadata.get("action")
                        or metadata.get("feedback_type")
                        or metadata.get("source_event")
                        or ""
                    ),
                )
            )
            stable_digest = uuid.uuid5(uuid.NAMESPACE_URL, stable_identity).hex
            event["ingest_key"] = stable_digest
            canonical.append(event)
            keys_by_index.append(bootstrap_key)
        if not canonical:
            return []
        receipt = await event_ingress.accept_batch(
            canonical,
            producer=f"source-{source}",
        )
        if receipt.rejected:
            reasons = "; ".join(item.error for item in receipt.items if item.error)
            raise RuntimeError(f"source event ingress rejected canonical event: {reasons}")
        return [
            keys_by_index[item.index]
            for item in receipt.items
            if (item.inserted or item.duplicate) and keys_by_index[item.index]
        ]

    async def _restart_background_tasks_after_event_recovery(
        *,
        run_post_reload_llm_work: bool = True,
        resume_execution_lanes: bool = True,
        recover_event_owner_synchronously: bool = True,
    ) -> None:
        """Fence/admit durable owners, then restart runtime periodic tasks.

        Hot reload keeps the default synchronous recovery after draining the
        old owner and publishing the replacement runtime.  Process startup is
        the sole caller that opts out: it admits recovery into the app-owned
        scheduler without awaiting provider-backed pipeline consumption.
        """
        _bind_runtime_lane_dependencies()
        if resume_execution_lanes:
            await _prepare_event_owners()
            await feedback_batch_scheduler.resume(
                recover=recover_event_owner_synchronously,
            )
            if not recover_event_owner_synchronously:
                start_recovery = getattr(
                    feedback_batch_scheduler,
                    "start_background_recovery",
                    None,
                )
                if callable(start_recovery):
                    app.state.event_recovery_task = start_recovery()
                else:  # compatibility with narrow injected scheduler fakes
                    feedback_batch_scheduler.schedule()
                    app.state.event_recovery_task = None
        if os.environ.get("OPENBILICLAW_FULL_WORKER", "").strip() == "1":
            logger.info(
                "External full worker active; API periodic background loops delegated"
            )
            return
        restart = ctx.restart_background_tasks
        try:
            restart_signature = inspect.signature(restart)
            supports_post_reload_flag = (
                "run_post_reload_llm_work" in restart_signature.parameters
                or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in restart_signature.parameters.values()
                )
            )
        except (TypeError, ValueError):
            supports_post_reload_flag = True
        if supports_post_reload_flag:
            await restart(
                app,
                run_post_reload_llm_work=run_post_reload_llm_work,
            )
        else:  # compatibility with narrow injected test runtimes
            await restart(app)

    async def _rebuild_runtime_with_lane_handoff(
        new_config: Any,
        *,
        run_post_reload_llm_work: bool = True,
        resume_execution_lanes: bool = True,
        after_rebuild: Any = None,
    ) -> None:
        """Quiesce old owners before publishing and recovering a new runtime."""
        async with config_runtime_reload_lock:
            await feedback_batch_scheduler.pause_and_drain()
            dialogue_paused = False
            try:
                await dialogue_execution_coordinator.pause_and_drain(
                    timeout=_DIALOGUE_EXECUTION_DRAIN_TIMEOUT_SECONDS
                )
                dialogue_paused = True
                await ctx.rebuild_from_config(_pin_active_runtime_config(new_config))
                if callable(after_rebuild):
                    after_rebuild()
                await _restart_background_tasks_after_event_recovery(
                    run_post_reload_llm_work=run_post_reload_llm_work,
                    resume_execution_lanes=resume_execution_lanes,
                )
            except BaseException:
                # Rebuild is atomic: on construction failure ctx still exposes the
                # old runtime; after publication it exposes the new one. Resolve at
                # resume time in both cases and never leave the lane paused.
                with suppress(Exception):
                    await _restart_background_tasks_after_event_recovery(
                        run_post_reload_llm_work=run_post_reload_llm_work,
                        resume_execution_lanes=resume_execution_lanes,
                    )
                raise
            finally:
                # Guided init may keep event owners paused, but the independent
                # chat lane must always resume against the current/rolled-back ctx.
                if dialogue_paused:
                    await dialogue_execution_coordinator.resume()

    # Stable, app-owned handoff seam used by every internal rebuild caller.
    # Keeping it on app.state also makes drain/publication invariants directly
    # testable without forcing a config.toml write through the HTTP surface.
    app.state._rebuild_runtime_with_lane_handoff = _rebuild_runtime_with_lane_handoff

    def _set_config_apply_status(
        state: Literal["idle", "queued", "applying", "applied", "failed"],
        message: str,
        *,
        error: str = "",
    ) -> None:
        nonlocal config_apply_state, config_apply_message
        nonlocal config_apply_error, config_apply_updated_at
        config_apply_state = state
        config_apply_message = message
        config_apply_error = error
        config_apply_updated_at = datetime.now(UTC).isoformat()

    def _config_apply_status_response() -> ConfigApplyStatusResponse:
        return ConfigApplyStatusResponse(
            state=config_apply_state,
            requested_revision=config_apply_revision,
            applied_revision=config_applied_revision,
            message=config_apply_message,
            error=config_apply_error,
            updated_at=config_apply_updated_at,
        )

    def _config_apply_busy() -> bool:
        task_running = config_apply_task is not None and not config_apply_task.done()
        return (
            task_running
            or config_apply_pending is not None
            or config_apply_state
            in {
                "queued",
                "applying",
            }
        )

    def _config_reload_error(exc: BaseException) -> str:
        if isinstance(exc, TimeoutError):
            return "后台对话在 25 分钟内仍未整理完成，运行时未能安全切换"
        return str(exc).strip() or type(exc).__name__

    async def _apply_runtime_config_revision(item: _QueuedConfigApply) -> str:
        """Apply one persisted revision without owning the config-file lock."""
        was_degraded = bool(getattr(ctx, "degraded", False))
        recovered_from_degraded = False

        def _after_config_runtime_rebuilt() -> None:
            nonlocal recovered_from_degraded
            if not was_degraded:
                return
            _complete_degraded_runtime_recovery()
            recovered_from_degraded = True

        try:
            await _rebuild_runtime_with_lane_handoff(
                item.config,
                run_post_reload_llm_work=item.run_post_reload_llm_work,
                after_rebuild=_after_config_runtime_rebuilt,
            )
        except Exception:
            if not recovered_from_degraded:
                raise
            logger.exception("Degraded runtime recovered, but background task restart failed")
            message = (
                f"配置已保存到 {item.saved_path}。后端已原地恢复；"
                "部分后台任务启动失败，将在后续配置刷新时重试，"
                "不影响继续初始化。"
            )
        else:
            message = f"配置已保存到 {item.saved_path}。"
            if was_degraded:
                message += " 后端已从降级模式原地恢复。"
            message += (
                " 除需完全重启的字段外，其余运行时组件已热重载。"
                if item.restart_required
                else " 运行时组件已热重载，新配置立即生效，无需重启。"
            )
        logger.info("Config hot-reload succeeded: revision=%d", item.revision)
        return message

    async def _restore_runtime_after_failed_config_apply() -> None:
        """Bring the in-memory runtime back to the persisted last-good config.

        ``RuntimeContext.rebuild_from_config`` publishes the new component set
        before the background-task restart step.  If that later step fails, the
        config file rollback alone would leave ``ctx.config`` describing the
        rejected candidate.  Rebuilding the last-good config is best effort: a
        second failure is logged, while the queue still reports the original
        apply error and keeps the on-disk rollback semantics.
        """
        try:
            await _rebuild_runtime_with_lane_handoff(
                copy.deepcopy(config_last_good),
                run_post_reload_llm_work=False,
            )
        except Exception:
            logger.critical(
                "Queued config rollback restored the file but not the in-memory runtime",
                exc_info=True,
            )

    async def _run_config_apply_queue() -> None:
        """Apply the newest queued revision; intermediate pending saves coalesce."""
        nonlocal config_apply_task, config_apply_pending
        nonlocal config_applied_revision, config_last_good
        from openbiliclaw.config import save_config
        from openbiliclaw.network import set_outbound_proxy

        try:
            while config_apply_pending is not None:
                item = config_apply_pending
                config_apply_pending = None
                _set_config_apply_status(
                    "applying",
                    f"正在后台应用配置修订 {item.revision}。",
                )
                runtime_apply_started = False
                try:
                    proxy_cfg = getattr(item.config, "network", None)
                    set_outbound_proxy(
                        str(getattr(proxy_cfg, "proxy", "") or ""),
                        mode=str(getattr(proxy_cfg, "mode", "system") or "system"),
                    )
                    runtime_apply_started = True
                    message = await _apply_runtime_config_revision(item)
                except asyncio.CancelledError:
                    config_apply_pending = item
                    _set_config_apply_status(
                        "queued",
                        f"配置修订 {item.revision} 已保存，将在后端重启后生效。",
                    )
                    raise
                except Exception as exc:
                    logger.exception(
                        "Queued config hot-reload failed: revision=%d",
                        item.revision,
                    )
                    error = _config_reload_error(exc)
                    async with _CONFIG_SAVE_LOCK:
                        if config_apply_pending is not None:
                            _set_config_apply_status(
                                "queued",
                                (
                                    f"配置修订 {item.revision} 应用失败；"
                                    f"正在改用更新的修订 {config_apply_pending.revision}。"
                                ),
                                error=error,
                            )
                            continue
                        try:
                            restored_path = save_config(
                                _preserve_persisted_restart_fields(config_last_good)
                            )
                            _snapshot_config_file(restored_path)
                            proxy_cfg = getattr(config_last_good, "network", None)
                            set_outbound_proxy(
                                str(getattr(proxy_cfg, "proxy", "") or ""),
                                mode=str(getattr(proxy_cfg, "mode", "system") or "system"),
                            )
                            if runtime_apply_started:
                                await _restore_runtime_after_failed_config_apply()
                        except Exception as restore_exc:
                            logger.critical(
                                "Queued config rollback failed after hot-reload exception",
                                exc_info=True,
                            )
                            failure_message = (
                                "后台热重载失败，且无法恢复最后一次已生效配置；"
                                "请检查 config.toml 与后端日志。"
                            )
                            _set_config_apply_status(
                                "failed",
                                failure_message,
                                error=str(restore_exc).strip() or type(restore_exc).__name__,
                            )
                        else:
                            failure_message = (
                                f"配置修订 {item.revision} 热重载失败（{error[:200]}），"
                                "已恢复最后一次生效配置。"
                            )
                            _set_config_apply_status(
                                "failed",
                                failure_message,
                                error=error,
                            )
                    with suppress(Exception):
                        await ctx.event_hub.publish(
                            {
                                "type": "config_reload_failed",
                                "revision": item.revision,
                                "message": config_apply_message,
                            }
                        )
                else:
                    config_last_good = copy.deepcopy(item.config)
                    config_applied_revision = item.revision
                    if config_apply_pending is None:
                        _set_config_apply_status("applied", message)
                    else:
                        _set_config_apply_status(
                            "queued",
                            (
                                f"配置修订 {item.revision} 已生效；"
                                f"修订 {config_apply_pending.revision} 等待应用。"
                            ),
                        )
                    with suppress(Exception):
                        await ctx.event_hub.publish(
                            {
                                "type": "config_reloaded",
                                "revision": item.revision,
                                "message": message,
                            }
                        )
        finally:
            config_apply_task = None
            app.state.config_apply_task = None

    def _enqueue_config_apply(item: _QueuedConfigApply) -> None:
        nonlocal config_apply_pending, config_apply_task
        config_apply_pending = item
        _set_config_apply_status(
            "queued",
            f"配置修订 {item.revision} 已保存，后端空闲后自动应用。",
        )
        if config_apply_task is None or config_apply_task.done():
            config_apply_task = asyncio.create_task(
                _run_config_apply_queue(),
                name="config-apply",
            )
            app.state.config_apply_task = config_apply_task

    def _is_feedback_event(event: dict[str, Any]) -> bool:
        return str(event.get("event_type") or event.get("type") or "").strip() == "feedback"

    def _is_retraction_feedback_event(event: dict[str, Any]) -> bool:
        if not _is_feedback_event(event):
            return False
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            metadata = _event_row_metadata(event)
        return str(metadata.get("feedback_type") or "").strip().lower() == "retraction"

    def _load_source_bootstrap_state() -> dict[str, object]:
        from openbiliclaw.sources.bootstrap_state import (
            default_source_bootstrap_state,
            normalize_source_bootstrap_state,
        )

        load_state = getattr(ctx.memory_manager, "load_source_bootstrap_state", None)
        if not callable(load_state):
            return default_source_bootstrap_state()
        with suppress(Exception):
            return normalize_source_bootstrap_state(load_state())
        return default_source_bootstrap_state()

    def _filter_new_source_bootstrap_items(
        source: str,
        items: list[dict[str, Any]],
        key_func: Callable[[dict[str, Any]], str],
    ) -> tuple[list[dict[str, Any]], dict[int, str]]:
        """Filter bootstrap items that already propagated from an older task."""
        from openbiliclaw.sources.bootstrap_state import (
            as_string_list,
            source_bootstrap_state_key,
        )

        state = _load_source_bootstrap_state()
        state_key = source_bootstrap_state_key(source)
        seen = set(as_string_list(state.get(state_key, [])))
        batch_seen: set[str] = set()
        fresh: list[dict[str, Any]] = []
        fresh_keys_by_index: dict[int, str] = {}
        for item in items:
            key = key_func(item)
            if not key or key in seen or key in batch_seen:
                continue
            batch_seen.add(key)
            fresh_keys_by_index[len(fresh)] = key
            fresh.append(item)
        return fresh, fresh_keys_by_index

    def _mark_source_bootstrap_keys(
        source: str,
        keys: list[str],
        *,
        account_key: str = "",
    ) -> None:
        """Persist bootstrap keys that already entered the source event path."""
        if not keys and not (source in {"linuxdo", "weibo"} and account_key):
            return
        from datetime import UTC, datetime

        from openbiliclaw.sources.bootstrap_state import (
            SOURCE_SEEN_KEY_CAP,
            merge_seen_keys,
            source_bootstrap_state_key,
        )

        state_key = source_bootstrap_state_key(source)
        update_state = getattr(ctx.memory_manager, "update_source_bootstrap_state", None)
        if not callable(update_state):
            raise RuntimeError("source bootstrap state atomic updater is unavailable")

        def _mutate(state: dict[str, object]) -> dict[str, object]:
            state[state_key] = merge_seen_keys(
                state.get(state_key, []),
                keys,
                cap=SOURCE_SEEN_KEY_CAP,
            )
            if source == "linuxdo" and account_key:
                state["linuxdo_account_key"] = account_key
            if source == "weibo" and account_key:
                state["weibo_account_key"] = account_key
            state["last_source_bootstrap_sync_at"] = datetime.now(UTC).isoformat()
            return state

        # This is the projection checkpoint for source task results.  Keep it
        # strict: terminal completion must not proceed when this write fails;
        # stale-lease repair will replay the frozen canonical result instead.
        update_state(_mutate)

    fallback_chat_turns: dict[str, dict[str, Any]] = {}

    def _dialogue_confirmation_state_path() -> Any:
        from pathlib import Path

        data_dir = getattr(ctx.memory_manager, "_data_dir", None) or config.data_path
        return Path(data_dir) / "memory" / "dialogue_confirmation_state.json"

    def _normalize_dialogue_confirmation_state(raw: object) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raw = {}
        raw_objects = raw.get("objects", {})
        objects = raw_objects if isinstance(raw_objects, dict) else {}
        normalized_objects: dict[str, dict[str, str]] = {}
        for raw_ref, raw_item in objects.items():
            if not isinstance(raw_item, dict):
                continue
            ref = str(raw_ref).strip()
            if not ref:
                continue
            normalized_objects[ref] = {
                "last_asked_at": str(raw_item.get("last_asked_at", "")).strip(),
                "deferred_until": str(raw_item.get("deferred_until", "")).strip(),
            }
        return {
            "global_last_thrown_at": str(raw.get("global_last_thrown_at", "")).strip(),
            "objects": normalized_objects,
        }

    def _load_dialogue_confirmation_state() -> dict[str, Any]:
        path = _dialogue_confirmation_state_path()
        if not path.exists():
            return _normalize_dialogue_confirmation_state({})
        try:
            with open(path, encoding="utf-8") as state_file:
                return _normalize_dialogue_confirmation_state(json.load(state_file))
        except (OSError, ValueError):
            logger.warning("Failed to load dialogue confirmation state; using defaults")
            return _normalize_dialogue_confirmation_state({})

    def _update_dialogue_confirmation_state(
        mutate: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        from openbiliclaw.memory.json_state import update_json_state

        def apply(state: dict[str, Any]) -> dict[str, Any]:
            mutate(state)
            return state

        return update_json_state(
            _dialogue_confirmation_state_path(),
            default_factory=lambda: _normalize_dialogue_confirmation_state({}),
            normalize=_normalize_dialogue_confirmation_state,
            serialize=_normalize_dialogue_confirmation_state,
            mutate=apply,
        )

    def _defer_dialogue_confirmation(ref: str) -> str:
        _require_dialogue_settlement_worker()
        deferred_until = (
            datetime.now(UTC) + timedelta(hours=_CONFIRMATION_OBJECT_COOLDOWN_HOURS)
        ).isoformat()

        def mutate(state: dict[str, Any]) -> None:
            objects = cast("dict[str, dict[str, str]]", state.setdefault("objects", {}))
            item = objects.setdefault(ref, {"last_asked_at": "", "deferred_until": ""})
            item["deferred_until"] = deferred_until

        _update_dialogue_confirmation_state(mutate)
        return deferred_until

    def _parse_confirmation_timestamp(raw: object) -> datetime | None:
        value = str(raw or "").strip().replace("Z", "+00:00")
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _confirmation_deferred_until(
        state: dict[str, Any],
        *,
        ref: str,
    ) -> datetime | None:
        objects = state.get("objects", {})
        raw_item = objects.get(ref, {}) if isinstance(objects, dict) else {}
        item = raw_item if isinstance(raw_item, dict) else {}
        return _parse_confirmation_timestamp(item.get("deferred_until", ""))

    def _is_confirmation_deferred(
        state: dict[str, Any],
        *,
        ref: str,
        now: datetime,
    ) -> bool:
        deferred_until = _confirmation_deferred_until(state, ref=ref)
        return deferred_until is not None and now < deferred_until

    def _hypothesis_confirmation_items() -> list[dict[str, Any]]:
        from openbiliclaw.soul.identity import build_hash8_map

        loader = getattr(ctx.soul_engine, "_load_insights", None)
        if not callable(loader):
            return []
        try:
            hypotheses = list(loader())
        except Exception:
            logger.debug("Failed to load pending hypotheses", exc_info=True)
            return []
        by_title = {
            str(getattr(item, "hypothesis", "")).strip(): item
            for item in hypotheses
            if str(getattr(item, "hypothesis", "")).strip()
        }
        ref_map = build_hash8_map(list(by_title))
        settlement_reader = getattr(ctx.database, "get_card_settlement", None)
        items: list[dict[str, Any]] = []
        for ref, title in ref_map.items():
            hypothesis = by_title[title]
            try:
                confidence = float(getattr(hypothesis, "confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            if (
                bool(getattr(hypothesis, "validated", False))
                or confidence < _PENDING_HYPOTHESIS_MIN_CONFIDENCE
            ):
                continue
            if callable(settlement_reader) and settlement_reader(ref) is not None:
                continue
            items.append(
                {
                    "kind": "hypothesis",
                    "ref": ref,
                    "title": title,
                    "evidence_refs": [
                        str(value).strip()
                        for value in getattr(hypothesis, "evidence", [])
                        if str(value).strip()
                    ],
                    "confidence": confidence,
                    "created_at": str(getattr(hypothesis, "created_at", "") or ""),
                }
            )
        return items

    def _confusion_confirmation_item(confusion: Any) -> dict[str, Any] | None:
        try:
            confidence = float(getattr(confusion, "interpretation_confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < _PENDING_CONFUSION_MIN_CONFIDENCE:
            return None
        topic = str(getattr(confusion, "topic", "") or "").strip()
        observation = str(getattr(confusion, "observation", "") or "").strip()
        title = topic or observation or "有件事我还没看懂"
        return {
            "kind": "confusion",
            "ref": str(getattr(confusion, "id", "") or ""),
            "title": title,
            "observation": observation,
            "interpretation": str(getattr(confusion, "interpretation", "") or "").strip(),
            "evidence_refs": [
                str(value).strip()
                for value in getattr(confusion, "evidence_refs", [])
                if str(value).strip()
            ],
            "confidence": confidence,
            "created_at": "",
            "status": str(getattr(confusion, "status", "") or "").strip().lower(),
        }

    def _pending_confirmation_items(
        *,
        limit: int,
        session: str = "",
    ) -> list[dict[str, Any]]:
        def rank(item: dict[str, Any]) -> tuple[float, int, str]:
            return (
                -float(item.get("confidence", 0.0) or 0.0),
                0 if item.get("kind") == "confusion" else 1,
                str(item.get("ref", "")),
            )

        confirmation_state = _load_dialogue_confirmation_state()
        now = datetime.now(UTC)
        hypotheses = [
            item
            for item in sorted(_hypothesis_confirmation_items(), key=rank)
            if not _is_confirmation_deferred(
                confirmation_state,
                ref=item["ref"],
                now=now,
            )
        ]
        confusions: list[dict[str, Any]] = []
        confusion_manager = getattr(ctx.soul_engine, "_confusion_manager", None)
        if confusion_manager is not None:
            try:
                for confusion in confusion_manager.list_active():
                    item = _confusion_confirmation_item(confusion)
                    if item is not None:
                        confusions.append(item)
            except Exception:
                logger.debug("Failed to load pending confusions", exc_info=True)
        # A clarifying confusion owns the database's single global slot.  Keep
        # that exact item in the reserved seat across every UI session instead
        # of showing a different open row whose click can only return 409.
        confusions.sort(
            key=lambda item: (
                0 if item.get("status") == "clarifying" else 1,
                *rank(item),
            )
        )
        if confusions and confusions[0].get("status") == "clarifying":
            active = confusions[0]
            active_ref = str(active.get("ref", ""))
            normalized_session = session.strip()
            already_visible = bool(
                normalized_session
                and _get_chat_confirmation_turn(
                    ref=active_ref,
                    session=normalized_session,
                )
                is not None
            )
            confusions = [] if already_visible else [active]

        capacity = max(0, int(limit))
        # Reserve seats for confusions first (see _PENDING_CONFUSION_RESERVED_SLOTS
        # for why a single descending sort starves them), then fill the rest with
        # hypotheses, then hand any still-unused capacity back to the other kind.
        reserved = min(len(confusions), _PENDING_CONFUSION_RESERVED_SLOTS, capacity)
        picked = confusions[:reserved]
        picked += hypotheses[: capacity - len(picked)]
        if len(picked) < capacity:
            picked += confusions[reserved : reserved + (capacity - len(picked))]
        picked.sort(key=rank)
        return picked

    def _pending_confirmation_by_ref(ref: str) -> dict[str, Any] | None:
        normalized_ref = ref.strip()
        if not normalized_ref:
            return None
        for item in _hypothesis_confirmation_items():
            if item["ref"] == normalized_ref:
                return item
        confusion_manager = getattr(ctx.soul_engine, "_confusion_manager", None)
        if confusion_manager is None:
            return None
        try:
            confusion_id = int(normalized_ref)
        except ValueError:
            return None
        confusion = confusion_manager.get(confusion_id)
        if confusion is None or confusion.status not in {"open", "clarifying"}:
            return None
        return _confusion_confirmation_item(confusion)

    def _system_confirmation_window_open(
        state: dict[str, Any],
        *,
        now: datetime,
    ) -> bool:
        last = _parse_confirmation_timestamp(state.get("global_last_thrown_at", ""))
        return last is None or now - last >= timedelta(hours=_CONFIRMATION_GLOBAL_COOLDOWN_HOURS)

    def _system_confirmation_object_ready(
        state: dict[str, Any],
        *,
        ref: str,
        now: datetime,
    ) -> bool:
        if _is_confirmation_deferred(state, ref=ref, now=now):
            return False
        objects = state.get("objects", {})
        raw_item = objects.get(ref, {}) if isinstance(objects, dict) else {}
        item = raw_item if isinstance(raw_item, dict) else {}
        last = _parse_confirmation_timestamp(item.get("last_asked_at", ""))
        return last is None or now - last >= timedelta(hours=_CONFIRMATION_OBJECT_COOLDOWN_HOURS)

    def _mark_dialogue_confirmation_thrown(
        ref: str,
        *,
        now: datetime,
        preserve_existing: bool = False,
    ) -> None:
        timestamp = now.astimezone(UTC).isoformat()

        def mutate(state: dict[str, Any]) -> None:
            if not preserve_existing or not str(state.get("global_last_thrown_at", "")).strip():
                state["global_last_thrown_at"] = timestamp
            objects = cast("dict[str, dict[str, str]]", state.setdefault("objects", {}))
            item = objects.setdefault(ref, {"last_asked_at": "", "deferred_until": ""})
            if not preserve_existing or not str(item.get("last_asked_at", "")).strip():
                item["last_asked_at"] = timestamp

        _update_dialogue_confirmation_state(mutate)

    def _claim_dialogue_confirmation_throw(ref: str, *, now: datetime) -> bool:
        """Atomically reserve both persisted system-throw cooldown gates."""
        claimed = False
        timestamp = now.astimezone(UTC).isoformat()

        def mutate(state: dict[str, Any]) -> None:
            nonlocal claimed
            if not _system_confirmation_window_open(state, now=now):
                return
            if not _system_confirmation_object_ready(state, ref=ref, now=now):
                return
            state["global_last_thrown_at"] = timestamp
            objects = cast("dict[str, dict[str, str]]", state.setdefault("objects", {}))
            item = objects.setdefault(ref, {"last_asked_at": "", "deferred_until": ""})
            item["last_asked_at"] = timestamp
            claimed = True

        _update_dialogue_confirmation_state(mutate)
        return claimed

    def _get_chat_confirmation_turn(*, ref: str, session: str) -> dict[str, Any] | None:
        getter = _chat_db_method("get_chat_confirmation_turn")
        if getter is not None:
            return cast("dict[str, Any] | None", getter(ref=ref, session=session))
        for row in reversed(list(fallback_chat_turns.values())):
            payload = row.get("payload", {})
            if (
                row.get("session") == session
                and row.get("subject_id") == ref
                and isinstance(payload, dict)
                and payload.get("ref") == ref
                and (
                    payload.get("type") == "question"
                    or (
                        payload.get("type") == "card"
                        and payload.get("state") in {"pending", "discussing"}
                    )
                )
            ):
                return dict(row)
        return None

    def _get_chat_confirmation_attachment(
        *,
        attached_to_turn_id: str,
        session: str,
    ) -> dict[str, Any] | None:
        getter = _chat_db_method("get_chat_confirmation_attachment")
        if getter is not None:
            return cast(
                "dict[str, Any] | None",
                getter(attached_to_turn_id=attached_to_turn_id, session=session),
            )
        for row in fallback_chat_turns.values():
            payload = row.get("payload", {})
            if (
                row.get("session") == session
                and isinstance(payload, dict)
                and payload.get("attached_to_turn_id") == attached_to_turn_id
            ):
                return dict(row)
        return None

    def _normalize_chat_scope(scope: str) -> str:
        normalized = scope.strip().lower()
        if normalized in {
            "chat",
            "delight",
            "probe",
            "avoidance_probe",
            "confusion",
            "hypothesis",
        }:
            return normalized
        return "chat"

    def _normalize_chat_turn(row: dict[str, Any]) -> ChatTurnOut:
        raw_payload = row.get("payload", {})
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except (json.JSONDecodeError, TypeError, ValueError):
                raw_payload = {}
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        return ChatTurnOut(
            turn_id=str(row.get("turn_id", "")),
            session=str(row.get("session", "popup") or "popup"),
            scope=_normalize_chat_scope(str(row.get("scope", "chat"))),
            subject_id=str(row.get("subject_id", "") or ""),
            subject_title=str(row.get("subject_title", "") or ""),
            reply_to_turn_id=str(row.get("reply_to_turn_id", "") or ""),
            message=str(row.get("message", "") or ""),
            reply=str(row.get("reply", "") or ""),
            status=str(row.get("status", "pending") or "pending"),
            error=str(row.get("error", "") or ""),
            payload=payload,
            created_at=str(row.get("created_at", "") or ""),
            updated_at=str(row.get("updated_at", "") or ""),
        )

    def _chat_db_method(name: str) -> Any | None:
        method = getattr(ctx.database, name, None)
        return method if callable(method) else None

    def _dialogue_settlement_queue() -> Any:
        queue = getattr(ctx, "dialogue_settlement_queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="Dialogue settlement queue not ready.")
        return queue

    def _dialogue_queue_ready_for_interactive_submission() -> bool:
        """Keep short clicks out of a queue currently occupied by LLM work."""
        queue = _dialogue_settlement_queue()
        ready = getattr(queue, "ready_for_interactive_submission", None)
        return True if ready is None else bool(ready)

    def _dialogue_queue_peek_anchor(
        *,
        target_kind: str = "",
        target_ref: str = "",
    ) -> Any:
        """Read the admission head without retaining a queue reference."""
        queue = _dialogue_settlement_queue()
        peek = getattr(queue, "peek_anchor", None)
        if callable(peek):
            return peek(target_kind=target_kind, target_ref=target_ref)
        registry = getattr(queue, "registry", None)
        peek_registry = getattr(registry, "peek", None)
        if callable(peek_registry):
            return peek_registry(target_kind=target_kind, target_ref=target_ref)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "dialogue_busy",
                "message": "Dialogue context validation is not ready.",
            },
        )

    def _dialogue_context_error(
        status_code: int,
        code: str,
        message: str,
        *,
        retry_after: str | None = None,
    ) -> NoReturn:
        headers = {"Retry-After": retry_after} if retry_after else None
        raise HTTPException(
            status_code=status_code,
            detail={"code": code, "message": message},
            headers=headers,
        )

    def _row_payload(row: Mapping[str, object]) -> dict[str, object]:
        raw_payload = row.get("payload", {})
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except (json.JSONDecodeError, TypeError, ValueError):
                raw_payload = {}
        return dict(raw_payload) if isinstance(raw_payload, Mapping) else {}

    def _stored_dialogue_binding(row: Mapping[str, object]) -> Any | None:
        from openbiliclaw.soul.dialogue_turn_context import (
            DialogueBindingError,
            DialogueTurnBinding,
        )

        raw_binding = _row_payload(row).get("dialogue_binding")
        if not isinstance(raw_binding, Mapping):
            return None
        try:
            return DialogueTurnBinding.from_mapping(raw_binding)
        except DialogueBindingError:
            # Legacy/corrupt rows are intentionally readable as unbound. They
            # never become evidence for a new target.
            logger.warning(
                "Ignoring invalid stored dialogue binding for turn=%r",
                row.get("turn_id"),
            )
            return None

    def _binding_from_turn(turn: ChatTurnOut) -> Any | None:
        return _stored_dialogue_binding({"payload": turn.payload, "turn_id": turn.turn_id})

    def _canonical_context_for_target(reply_to_turn_id: str) -> tuple[Any, str, str, str]:
        """Resolve a target row plus exact admission generation synchronously."""
        from openbiliclaw.soul.dialogue_learn_queue import (
            AnchorAbsent,
            AnchorFailed,
            AnchorNotApplicable,
            AnchorPersisted,
            AnchorReserved,
        )
        from openbiliclaw.soul.dialogue_turn_context import DialogueTurnContext

        normalized_target = reply_to_turn_id.strip()
        target = _read_chat_turn_row(normalized_target)
        if target is None:
            _dialogue_context_error(
                404,
                "reply_target_not_found",
                "The card or question you selected is no longer available.",
            )
        assert target is not None
        if str(target.get("status", "")) != "completed":
            _dialogue_context_error(
                409,
                "reply_target_inactive",
                "That card or question is still processing.",
            )
        payload = _row_payload(target)
        payload_type = str(payload.get("type", "")).strip().lower()
        kind = str(payload.get("kind", "")).strip().lower()
        ref = str(payload.get("ref", "") or target.get("subject_id", "")).strip()
        title = str(payload.get("title", "") or target.get("subject_title", "")).strip()
        state = str(payload.get("state", "")).strip().lower()
        if payload_type == "card" and kind == "hypothesis":
            if state != "discussing":
                _dialogue_context_error(
                    409,
                    "reply_target_inactive",
                    "Discuss the hypothesis card before replying to it.",
                )
            source_type = "card"
            canonical_scope = "chat"
            canonical_subject_id = ""
            canonical_subject_title = ""
        elif payload_type == "question" and kind == "confusion":
            if state in {"resolved", "dismissed", "expired"}:
                _dialogue_context_error(
                    409,
                    "reply_target_inactive",
                    "That question has already been settled.",
                )
            source_type = "question"
            canonical_scope = "confusion"
            canonical_subject_id = ref
            canonical_subject_title = title
        else:
            _dialogue_context_error(
                422,
                "invalid_reply_target",
                "reply_to_turn_id must identify a completed card or question.",
            )
        if not ref or not title:
            _dialogue_context_error(
                422,
                "invalid_reply_target",
                "The selected dialogue target has no canonical identity.",
            )

        snapshot = _dialogue_queue_peek_anchor(target_kind=kind, target_ref=ref)
        if isinstance(snapshot, AnchorReserved):
            _dialogue_context_error(
                409,
                "reply_target_processing",
                "That dialogue target is still being opened; try again shortly.",
                retry_after="2",
            )
        if isinstance(snapshot, (AnchorAbsent, AnchorFailed, AnchorNotApplicable)):
            _dialogue_context_error(
                409,
                "reply_target_inactive",
                "That dialogue target is no longer active.",
            )
        if not isinstance(snapshot, AnchorPersisted):
            _dialogue_context_error(
                409,
                "reply_target_inactive",
                "That dialogue target is no longer active.",
            )

        anchor_manager = getattr(ctx.soul_engine, "_dialogue_anchor_manager", None)
        active = anchor_manager.current() if anchor_manager is not None else None
        if active is not None and (
            active.kind != kind or active.ref != ref or active.generation != snapshot.generation
        ):
            _dialogue_context_error(
                409,
                "reply_target_inactive",
                "That dialogue target was replaced before the reply was sent.",
            )
        origin_turn_id = (
            str(active.origin_turn_id).strip()
            if active is not None and active.kind == kind and active.ref == ref
            else normalized_target
        )
        evidence_refs = payload.get("evidence_refs", [])
        evidence_labels = evidence_refs if isinstance(evidence_refs, list) else []
        try:
            context = DialogueTurnContext(
                reply_to_turn_id=normalized_target,
                source_type=source_type,
                kind=kind,
                ref=ref,
                generation=snapshot.generation,
                anchor_origin_turn_id=origin_turn_id,
                title=title,
                evidence_labels=tuple(str(item) for item in evidence_labels),
                captured_at=datetime.now(UTC).isoformat(),
            )
        except ValueError as exc:
            _dialogue_context_error(422, "invalid_reply_target", str(exc))
        return context, canonical_scope, canonical_subject_id, canonical_subject_title

    def _unbound_dialogue_binding(*, attached_confirmation: bool) -> Any:
        from openbiliclaw.soul.dialogue_learn_queue import (
            AnchorAbsent,
            AnchorFailed,
            AnchorNotApplicable,
            AnchorPersisted,
            AnchorReserved,
        )
        from openbiliclaw.soul.dialogue_turn_context import DialogueTurnBinding

        if attached_confirmation:
            return DialogueTurnBinding.detached()
        snapshot = _dialogue_queue_peek_anchor()
        if isinstance(snapshot, (AnchorPersisted, AnchorReserved)):
            return DialogueTurnBinding.detached()
        if isinstance(snapshot, (AnchorAbsent, AnchorFailed, AnchorNotApplicable)):
            return DialogueTurnBinding.ordinary()
        return DialogueTurnBinding.ordinary()

    def _stored_request_matches(row: Mapping[str, object], payload: ChatTurnIn) -> bool:
        """Compare a retry with the immutable request/binding already stored."""
        stored_binding = _stored_dialogue_binding(row)
        if str(row.get("message", "")) != payload.message.strip():
            return False
        if str(row.get("session", "popup") or "popup") != (payload.session.strip() or "popup"):
            return False
        if str(row.get("reply_to_turn_id", "") or "") != payload.reply_to_turn_id.strip():
            return False
        if stored_binding is None or stored_binding.mode.value != "bound":
            return (
                _normalize_chat_scope(str(row.get("scope", "chat")))
                == _normalize_chat_scope(payload.scope)
                and str(row.get("subject_id", "") or "") == payload.subject_id.strip()
                and str(row.get("subject_title", "") or "") == payload.subject_title.strip()
            )
        context = stored_binding.context
        if context is None:
            return False
        if context.kind == "hypothesis":
            return (
                _normalize_chat_scope(payload.scope) == "chat"
                and not payload.subject_id.strip()
                and not payload.subject_title.strip()
            )
        return (
            _normalize_chat_scope(payload.scope) in {"chat", "confusion"}
            and payload.subject_id.strip() in {"", context.ref}
            and payload.subject_title.strip() in {"", context.title}
        )

    def _context_preview(binding: Any) -> DialogueContextPreview:
        context = binding.context
        if context is None:
            raise RuntimeError("Only bound bindings have context previews")
        return DialogueContextPreview(
            active=True,
            reply_to_turn_id=context.reply_to_turn_id,
            source_type=context.source_type,
            kind=context.kind,
            generation=context.generation,
            title=context.title,
            evidence_labels=list(context.evidence_labels),
            context_digest=binding.context_digest,
        )

    def _raise_dialogue_busy() -> NoReturn:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "dialogue_busy",
                "message": "后台正在整理上一段对话，这条内容会自动重试。",
            },
            headers={"Retry-After": "2"},
        )

    def _require_dialogue_settlement_worker() -> None:
        require = getattr(
            _dialogue_settlement_queue(),
            "require_dialogue_settlement_worker",
            None,
        )
        if not callable(require):
            raise RuntimeError("Dialogue settlement worker guard is not installed")
        require()

    async def _submit_dialogue_settlement(
        kind: DialogueJobKind,
        payload: dict[str, object],
        *,
        wait_seconds: float = 1.0,
    ) -> DialogueJobResult | None:
        """Submit once and preserve the in-memory job when the HTTP wait expires.

        One second is calibrated for local SQLite/JSON effects, not remote LLM
        work. A longer queue head returns the established processing contract.
        """
        queue = _dialogue_settlement_queue()
        job = queue.submit(kind, payload, completion=True)
        if job is None or job.completion is None:
            raise HTTPException(status_code=503, detail="Dialogue settlement queue is paused.")
        try:
            return await asyncio.wait_for(
                asyncio.shield(job.completion),
                timeout=wait_seconds,
            )
        except TimeoutError:
            return None

    async def _submit_dialogue_settlement_required(
        kind: DialogueJobKind,
        payload: dict[str, object],
    ) -> DialogueJobResult:
        """Await a required local command without imposing the HTTP fast-path budget."""
        queue = _dialogue_settlement_queue()
        submit_and_wait = getattr(queue, "submit_and_wait", None)
        if not callable(submit_and_wait):
            raise RuntimeError("Dialogue settlement queue cannot await required work")
        return cast(
            "DialogueJobResult",
            await submit_and_wait(kind, payload),
        )

    def _read_chat_turn_row(turn_id: str) -> dict[str, Any] | None:
        get_chat_turn = _chat_db_method("get_chat_turn")
        if get_chat_turn is not None:
            return cast("dict[str, Any] | None", get_chat_turn(turn_id))
        row = fallback_chat_turns.get(turn_id)
        return dict(row) if row else None

    def _get_chat_turn_row(turn_id: str) -> dict[str, Any] | None:
        return _read_chat_turn_row(turn_id)

    async def _reconcile_orphan_confusion_claims(*, limit: int = 200) -> int:
        """Release crash-gap clarifying rows whose claimed turn was never created."""
        list_confusions = getattr(ctx.database, "list_confusions", None)
        settlement_queue = getattr(ctx, "dialogue_settlement_queue", None)
        if not callable(list_confusions) or not callable(
            getattr(settlement_queue, "submit_and_wait", None)
        ):
            return 0
        rows = list_confusions(statuses=["clarifying"], limit=max(1, int(limit)))
        released = 0
        from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind

        for row in rows:
            ask_turn_id = str(row.get("ask_turn_id", "")).strip()
            if ask_turn_id and _read_chat_turn_row(ask_turn_id) is not None:
                continue
            completion = await _submit_dialogue_settlement_required(
                DialogueJobKind.CONFUSION_OPEN_SYNC,
                {
                    "operation": "reconcile_orphan",
                    "confusion_id": int(row["id"]),
                    "expected_ask_turn_id": ask_turn_id,
                    "minimum_age_seconds": _CONFUSION_ORPHAN_CLAIM_MIN_AGE_SECONDS,
                },
            )
            settlement = completion.settlement or {}
            released += int(bool(settlement.get("released", False)))
        return released

    def _submit_chat_card_reconcile(row: dict[str, Any]) -> None:
        raw_payload = row.get("payload", {})
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except (json.JSONDecodeError, TypeError, ValueError):
                return
        if not isinstance(raw_payload, dict) or raw_payload.get("type") != "card":
            return
        state = str(raw_payload.get("state", "")).strip().lower()
        if state in _TERMINAL_CARD_STATES:
            return
        ref = str(raw_payload.get("ref", "")).strip()
        kind = str(raw_payload.get("kind", "hypothesis")).strip().lower()
        settlement_queue = getattr(ctx, "dialogue_settlement_queue", None)
        submit = getattr(settlement_queue, "submit", None)
        if not ref or not kind or not callable(submit):
            return
        from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind

        try:
            submit(
                DialogueJobKind.CARD_RECONCILE,
                {
                    "ref": ref,
                    "kind": kind,
                    "turn_id": str(row.get("turn_id", "")),
                    "source": "chat_turn_get",
                },
            )
        except Exception:
            logger.warning("card.reconcile submission failed for ref=%r", ref, exc_info=True)

    def _reconcile_chat_card_row(row: dict[str, Any]) -> bool:
        """Repair one orphan discussion from inside the settlement worker."""
        _require_dialogue_settlement_worker()
        raw_payload = row.get("payload", {})
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except (json.JSONDecodeError, TypeError, ValueError):
                return False
        if not isinstance(raw_payload, dict) or raw_payload.get("type") != "card":
            return False
        if str(raw_payload.get("state", "")) != "discussing":
            return False
        anchor_manager = getattr(ctx.soul_engine, "_dialogue_anchor_manager", None)
        active_anchor = anchor_manager.current() if anchor_manager is not None else None
        if active_anchor is not None and active_anchor.origin_turn_id == str(
            row.get("turn_id", "")
        ):
            return False
        update_payload = _chat_db_method("update_chat_turn_payload_state")
        return bool(
            update_payload is not None
            and update_payload(
                str(row.get("turn_id", "")),
                expected_state="discussing",
                new_state="pending",
            )
        )

    def _list_chat_turn_rows(
        *,
        session: str = "popup",
        scope: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        list_chat_turns = _chat_db_method("list_chat_turns")
        if list_chat_turns is not None:
            rows = cast(
                "list[dict[str, Any]]",
                list_chat_turns(session=session, scope=scope, limit=limit),
            )
            return rows
        rows = [
            dict(row)
            for row in fallback_chat_turns.values()
            if row.get("session") == session and (not scope or row.get("scope") == scope)
        ]
        rows.sort(key=lambda row: (str(row.get("created_at", "")), str(row.get("turn_id", ""))))
        return rows[-max(1, int(limit)) :]

    def _create_chat_turn_row(
        payload: ChatTurnIn,
        *,
        turn_id: str,
        structured_payload: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        create_chat_turn = _chat_db_method("create_chat_turn")
        if create_chat_turn is not None:
            return cast(
                "dict[str, Any]",
                create_chat_turn(
                    turn_id=turn_id,
                    session=payload.session.strip() or "popup",
                    scope=_normalize_chat_scope(payload.scope),
                    subject_id=payload.subject_id.strip(),
                    subject_title=payload.subject_title.strip(),
                    message=payload.message.strip(),
                    reply_to_turn_id=payload.reply_to_turn_id.strip(),
                    payload=structured_payload or {},
                ),
            )

        from datetime import datetime

        now = datetime.now().isoformat(sep=" ")
        fallback_chat_turns.setdefault(
            turn_id,
            {
                "turn_id": turn_id,
                "session": payload.session.strip() or "popup",
                "scope": _normalize_chat_scope(payload.scope),
                "subject_id": payload.subject_id.strip(),
                "subject_title": payload.subject_title.strip(),
                "reply_to_turn_id": payload.reply_to_turn_id.strip(),
                "message": payload.message.strip(),
                "status": "pending",
                "reply": "",
                "error": "",
                "payload": dict(structured_payload or {}),
                "created_at": now,
                "updated_at": now,
            },
        )
        return dict(fallback_chat_turns[turn_id])

    def _confusion_question_reply(item: dict[str, Any]) -> str:
        title = str(item.get("title", "")).strip() or "这件事"
        observation = str(item.get("observation", "")).strip()
        if observation:
            return f"我对「{title}」有点没看懂：{observation}。你愿意说说实际情况吗？"
        return f"我对「{title}」有点没看懂。你愿意说说实际情况吗？"

    async def _prepare_confusion_confirmation(
        item: dict[str, Any],
        *,
        turn_id: str,
        ignore_cooldown: bool,
    ) -> bool:
        """Schedule or retarget one confusion; report whether open became clarifying."""
        confusion_manager = getattr(ctx.soul_engine, "_confusion_manager", None)
        if confusion_manager is None:
            raise HTTPException(status_code=503, detail="Confusion manager not ready.")
        try:
            confusion_id = int(str(item.get("ref", "")))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Pending confirmation not found.") from exc
        confusion = confusion_manager.get(confusion_id)
        if confusion is None or confusion.status not in {"open", "clarifying"}:
            raise HTTPException(status_code=409, detail="Confusion is no longer open.")
        from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind

        if confusion.status == "open":
            completion = await _submit_dialogue_settlement_required(
                DialogueJobKind.CONFUSION_OPEN_SYNC,
                {
                    "operation": "schedule",
                    "confusion_id": confusion_id,
                    "ask_turn_id": turn_id,
                    "asked_at": datetime.now(UTC).isoformat(),
                    "ignore_cooldown": ignore_cooldown,
                },
            )
            settlement = completion.settlement or {}
            claimed = bool(settlement.get("claimed", False))
            if not claimed:
                raise HTTPException(
                    status_code=409,
                    detail="Another confusion already owns the clarifying slot.",
                )
            return True
        if confusion.ask_turn_id != turn_id:
            await _submit_dialogue_settlement_required(
                DialogueJobKind.CONFUSION_OPEN_SYNC,
                {
                    "operation": "retarget",
                    "confusion_id": confusion_id,
                    "ask_turn_id": turn_id,
                    "asked_at": datetime.now(UTC).isoformat(),
                },
            )
        return False

    async def _create_confirmation_turn(
        item: dict[str, Any],
        *,
        session: str,
        attached_to_turn_id: str = "",
        user_initiated: bool,
    ) -> tuple[ChatTurnOut, bool]:
        normalized_session = session.strip() or "popup"
        ref = str(item.get("ref", "")).strip()
        kind = str(item.get("kind", "")).strip()
        existing = _get_chat_confirmation_turn(ref=ref, session=normalized_session)
        if existing is not None and not attached_to_turn_id:
            if kind == "confusion":
                await _prepare_confusion_confirmation(
                    item,
                    turn_id=str(existing.get("turn_id", "")),
                    ignore_cooldown=user_initiated,
                )
            row = existing
            created = False
        else:
            if attached_to_turn_id:
                stable_key = f"{normalized_session}\0{attached_to_turn_id}"
                turn_id = f"confirmation-{uuid.uuid5(uuid.NAMESPACE_URL, stable_key).hex}"
            else:
                turn_id = f"confirmation-{uuid.uuid4().hex}"
            claimed_open = False
            if kind == "confusion":
                claimed_open = await _prepare_confusion_confirmation(
                    item,
                    turn_id=turn_id,
                    ignore_cooldown=user_initiated,
                )
            structured_payload: dict[str, object]
            if kind == "hypothesis":
                structured_payload = {
                    "type": "card",
                    "kind": "hypothesis",
                    "ref": ref,
                    "title": str(item.get("title", "")).strip(),
                    "evidence_refs": list(item.get("evidence_refs", [])),
                    "actions": list(_HYPOTHESIS_CARD_ACTIONS),
                    "state": "pending",
                }
                message = "阿b 的猜测"
                reply = ""
                scope = "hypothesis"
            elif kind == "confusion":
                structured_payload = {
                    "type": "question",
                    "kind": "confusion",
                    "ref": ref,
                    "title": str(item.get("title", "")).strip(),
                    "evidence_refs": list(item.get("evidence_refs", [])),
                    "state": "clarifying",
                }
                message = ""
                reply = _confusion_question_reply(item)
                scope = "confusion"
            else:
                raise HTTPException(status_code=404, detail="Pending confirmation not found.")
            if attached_to_turn_id:
                structured_payload["attached_to_turn_id"] = attached_to_turn_id
            creator = _chat_db_method("create_chat_confirmation_turn")
            try:
                if creator is not None:
                    row, created = creator(
                        turn_id=turn_id,
                        session=normalized_session,
                        scope=scope,
                        ref=ref,
                        title=str(item.get("title", "")).strip(),
                        message=message,
                        reply=reply,
                        payload=structured_payload,
                    )
                else:
                    internal = ChatTurnIn(
                        message=message,
                        turn_id=turn_id,
                        session=normalized_session,
                        scope=scope,
                        subject_id=ref,
                        subject_title=str(item.get("title", "")).strip(),
                    )
                    row = _create_chat_turn_row(
                        internal,
                        turn_id=turn_id,
                        structured_payload=structured_payload,
                    )
                    _complete_chat_turn_row(turn_id, reply=reply)
                    row = _get_chat_turn_row(turn_id) or row
                    created = True
            except Exception:
                if claimed_open:
                    from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind

                    await _submit_dialogue_settlement_required(
                        DialogueJobKind.CONFUSION_OPEN_SYNC,
                        {
                            "operation": "rollback",
                            "confusion_id": int(ref),
                            "ask_turn_id": "",
                        },
                    )
                raise
            if kind == "confusion" and str(row.get("turn_id", "")) != turn_id:
                from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind

                await _submit_dialogue_settlement_required(
                    DialogueJobKind.CONFUSION_OPEN_SYNC,
                    {
                        "operation": "retarget",
                        "confusion_id": int(ref),
                        "ask_turn_id": str(row.get("turn_id", "")),
                        "asked_at": datetime.now(UTC).isoformat(),
                    },
                )

        turn = _normalize_chat_turn(row)
        should_anchor = user_initiated or kind == "confusion"
        if should_anchor:
            from openbiliclaw.soul.dialogue_anchor import ENTRY_PENDING_OPEN
            from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind

            producer_source = (
                "pending_confusion_throw" if kind == "confusion" else "pending_probe_throw"
            )
            await _submit_dialogue_settlement_required(
                DialogueJobKind.ANCHOR_ESTABLISH,
                {
                    "target_kind": kind,
                    "target_ref": ref,
                    "origin_turn_id": turn.turn_id,
                    "entry": ENTRY_PENDING_OPEN,
                    "producer_source": producer_source,
                },
            )
        return turn, bool(created)

    async def _maybe_attach_system_confirmation(
        payload: ChatTurnIn,
        *,
        turn_id: str,
    ) -> ChatTurnOut | None:
        normalized_session = payload.session.strip() or "popup"
        existing_attachment = _get_chat_confirmation_attachment(
            attached_to_turn_id=turn_id,
            session=normalized_session,
        )
        now = datetime.now(UTC)
        if existing_attachment is not None:
            turn = _normalize_chat_turn(existing_attachment)
            ref = str(turn.payload.get("ref", "")).strip()
            if ref:
                _mark_dialogue_confirmation_thrown(
                    ref,
                    now=now,
                    preserve_existing=True,
                )
            return turn

        anchor_manager = getattr(ctx.soul_engine, "_dialogue_anchor_manager", None)
        if anchor_manager is not None and anchor_manager.current() is not None:
            return None
        state = _load_dialogue_confirmation_state()
        if not _system_confirmation_window_open(state, now=now):
            return None
        for item in _pending_confirmation_items(limit=200):
            ref = str(item.get("ref", "")).strip()
            if not _system_confirmation_object_ready(state, ref=ref, now=now):
                continue
            if _get_chat_confirmation_turn(ref=ref, session=normalized_session) is not None:
                continue
            if not _claim_dialogue_confirmation_throw(ref, now=now):
                return None
            try:
                turn, _created = await _create_confirmation_turn(
                    item,
                    session=normalized_session,
                    attached_to_turn_id=turn_id,
                    user_initiated=False,
                )
            except HTTPException as exc:
                if exc.status_code == 409:
                    return None
                raise
            if str(turn.payload.get("attached_to_turn_id", "")) != turn_id:
                return None
            return turn
        return None

    def _complete_chat_turn_row(turn_id: str, *, reply: str) -> bool:
        complete_chat_turn = _chat_db_method("complete_chat_turn")
        if complete_chat_turn is not None:
            changed = complete_chat_turn(turn_id, reply=reply)
            return True if changed is None else bool(changed)
        if (
            turn_id in fallback_chat_turns
            and str(fallback_chat_turns[turn_id].get("status", "")) == "pending"
        ):
            from datetime import datetime

            fallback_chat_turns[turn_id].update(
                {
                    "status": "completed",
                    "reply": reply,
                    "error": "",
                    "updated_at": datetime.now().isoformat(sep=" "),
                }
            )
            return True
        return False

    def _fail_chat_turn_row(turn_id: str, *, error: str, reply: str = "") -> bool:
        fail_chat_turn = _chat_db_method("fail_chat_turn")
        if fail_chat_turn is not None:
            changed = fail_chat_turn(turn_id, error=error, reply=reply)
            return True if changed is None else bool(changed)
        if (
            turn_id in fallback_chat_turns
            and str(fallback_chat_turns[turn_id].get("status", "")) == "pending"
        ):
            from datetime import datetime

            fallback_chat_turns[turn_id].update(
                {
                    "status": "failed",
                    "reply": reply,
                    "error": error,
                    "updated_at": datetime.now().isoformat(sep=" "),
                }
            )
            return True
        return False

    def _health_profile_ready() -> bool | None:
        soul_engine = getattr(ctx, "soul_engine", None)
        if soul_engine is None:
            return None
        is_ready_candidate = getattr(soul_engine, "is_profile_ready", None)
        if not callable(is_ready_candidate):
            return None
        is_ready_fn = cast("Callable[[], bool]", is_ready_candidate)
        try:
            return bool(is_ready_fn())
        except Exception:
            logger.debug("Health profile readiness check failed", exc_info=True)
            return None

    _lan_ip_value: str | None = None
    _lan_ip_checked_at = float("-inf")

    def _health_lan_ip() -> str | None:
        nonlocal _lan_ip_value, _lan_ip_checked_at
        if time.monotonic() - _lan_ip_checked_at < _LAN_IP_TTL_SECONDS:
            return _lan_ip_value
        _lan_ip_value = _detect_lan_ip()
        _lan_ip_checked_at = time.monotonic()
        return _lan_ip_value

    async def _fresh_lan_ip() -> str | None:
        """Detect the LAN IP now, bypassing the ``/api/health`` TTL cache.

        Opening the QR panel is a rare, user-initiated action, and a stale
        address there is worse than a marginally slower panel: right after a
        Wi-Fi switch the cached value still encodes a host the phone cannot
        reach. Detection shells out to ifconfig / ip, so it runs in a worker
        thread to keep the event loop responsive, and the result refreshes the
        shared cache that ``/api/health`` reads.
        """
        nonlocal _lan_ip_value, _lan_ip_checked_at
        _lan_ip_value = await asyncio.to_thread(_detect_lan_ip)
        _lan_ip_checked_at = time.monotonic()
        return _lan_ip_value

    # Embedding readiness is probed live (see _health_embedding_ready) and the
    # result cached here so frequent /api/health polls share one provider call.
    _embedding_probe_outcome: _EmbeddingProbeOutcome = "failed"
    _embedding_ready_checked_at = float("-inf")
    _embedding_ready_lock = asyncio.Lock()
    # Classified not-ready cause (v0.3.155+), cached on the same cadence so
    # init-status polls don't re-diagnose Ollama on every request.
    _embedding_diag_value: tuple[str, str] = ("ok", "")
    _embedding_diag_checked_at = float("-inf")
    _embedding_diag_lock = asyncio.Lock()

    def _expire_embedding_ready_cache() -> None:
        """Force the next readiness/diagnosis check to re-probe immediately."""
        nonlocal _embedding_ready_checked_at, _embedding_diag_checked_at
        _embedding_ready_checked_at = float("-inf")
        _embedding_diag_checked_at = float("-inf")

    def _embedding_ollama_target() -> tuple[str, str]:
        """(base_url, model) the embedding path would use for Ollama.

        Mirrors registry defaults: empty base_url → local daemon, empty
        model → bge-m3.
        """
        emb = getattr(getattr(getattr(ctx, "config", None), "llm", None), "embedding", None)
        base_url = str(getattr(emb, "base_url", "") or "").strip() or "http://127.0.0.1:11434/v1"
        model = str(getattr(emb, "model", "") or "").strip() or "bge-m3"
        return base_url, model

    def _embedding_probe_ttl(outcome: _EmbeddingProbeOutcome) -> float:
        return _EMBEDDING_READY_TTL_SECONDS if outcome == "ready" else _EMBEDDING_FAIL_TTL_SECONDS

    def _embedding_probe_result(outcome: _EmbeddingProbeOutcome, *, strict: bool) -> bool:
        if outcome == "ready":
            return True
        if outcome != "timed_out" or strict:
            return False
        emb = getattr(getattr(getattr(ctx, "config", None), "llm", None), "embedding", None)
        provider = str(getattr(emb, "provider", "") or "").strip().lower()
        if provider != "ollama":
            return False
        from openbiliclaw.runtime.ollama_supervisor import is_loopback

        base_url, _ = _embedding_ollama_target()
        return is_loopback(base_url)

    def _peek_embedding_ready(*, strict: bool = False) -> bool:
        """Read the last embedding outcome without starting provider I/O.

        Guided init already performs a strict pre-flight probe. While that run
        owns the LLM/embedding budget, status polling must remain observational
        and must not contend with or bill another provider request.
        """
        soul_engine = getattr(ctx, "soul_engine", None)
        service = getattr(soul_engine, "_embedding_service", None)
        if service is None:
            return False
        if not callable(getattr(service, "probe", None)):
            return True
        return _embedding_probe_result(_embedding_probe_outcome, strict=strict)

    async def _diagnose_embedding(ready: bool) -> tuple[str, str]:
        """Classify why embedding is not ready (``("ok", "")`` when it is).

        Cheap cases (disabled / misconfigured) are answered from config
        alone; the Ollama case runs a real classification (is the daemon
        up / is the model installed / does it load) behind a TTL cache.
        Non-Ollama providers get a generic ``provider_error`` — their
        failure detail already lands in logs and re-probing a cloud
        provider from a poll loop would burn quota.
        """
        nonlocal _embedding_diag_value, _embedding_diag_checked_at
        if ready:
            return ("ok", "")
        # An in-flight one-click repair is the freshest possible signal —
        # report live pull progress (no TTL cache: progress changes every
        # poll, and the classification is already known).
        pull_progress = _embedding_pull_progress_view()
        if pull_progress["running"]:
            return ("repairing", str(pull_progress["status_text"] or _repair_progress_detail()))
        emb = getattr(getattr(getattr(ctx, "config", None), "llm", None), "embedding", None)
        provider = str(getattr(emb, "provider", "") or "").strip()
        if not provider:
            return ("disabled", "")
        soul_engine = getattr(ctx, "soul_engine", None)
        service = getattr(soul_engine, "_embedding_service", None)
        if service is None:
            # Configured but the registry couldn't build it. Distinguish an
            # unknown provider NAME (e.g. a browser-translated value like
            # '奥拉玛' — re-pick in settings) from a known provider whose
            # build failed (usually missing key / base_url — fix credentials).
            from openbiliclaw.llm.registry import _EMBEDDING_CAPABLE_PROVIDERS

            if provider.lower() in _EMBEDDING_CAPABLE_PROVIDERS:
                return (
                    "misconfigured",
                    f"embedding 服务未能构建（provider={provider}）——"
                    "请到设置页检查该 provider 的 API Key / base_url 后重新保存。",
                )
            return (
                "misconfigured",
                f"embedding 配置无效（provider={provider!r}），"
                "请到设置页重新选择 provider 并保存。",
            )
        if provider.lower() != "ollama":
            return (
                "provider_error",
                f"embedding provider（{provider}）探测失败——"
                "请检查 API Key / base_url / 网络后重试。",
            )

        _diag_ttl = _EMBEDDING_FAIL_TTL_SECONDS
        if time.monotonic() - _embedding_diag_checked_at < _diag_ttl:
            return _embedding_diag_value
        async with _embedding_diag_lock:
            if time.monotonic() - _embedding_diag_checked_at < _diag_ttl:
                return _embedding_diag_value
            from openbiliclaw.llm.ollama_diagnostics import diagnose_ollama_embedding

            base_url, model = _embedding_ollama_target()
            try:
                diag = await asyncio.wait_for(
                    diagnose_ollama_embedding(base_url, model),
                    timeout=_EMBEDDING_PROBE_TIMEOUT_SECONDS,
                )
                if diag[0] == "error":
                    diag = ("provider_error", _provider_error_detail(diag[1]))
            except TimeoutError:
                diag = (
                    "provider_error",
                    "Ollama 诊断超时——服务可能正在冷加载模型，稍后自动重试。",
                )
            except Exception:
                logger.debug("Embedding diagnosis errored", exc_info=True)
                diag = ("provider_error", "embedding 诊断失败，请查看后端日志。")
            _embedding_diag_value = diag
            _embedding_diag_checked_at = time.monotonic()
            return diag

    def _peek_embedding_diagnosis(ready: bool) -> tuple[str, str]:
        """Return a non-I/O embedding diagnosis while guided init is active."""
        if ready:
            return ("ok", "")
        pull_progress = _embedding_pull_progress_view()
        if pull_progress["running"]:
            return ("repairing", str(pull_progress["status_text"] or _repair_progress_detail()))
        emb = getattr(getattr(getattr(ctx, "config", None), "llm", None), "embedding", None)
        provider = str(getattr(emb, "provider", "") or "").strip()
        if not provider:
            return ("disabled", "")
        if _embedding_diag_checked_at != float("-inf"):
            return _embedding_diag_value
        return ("checking", "初始化正在使用 AI 服务，完成后会自动重新检测向量服务。")

    async def _health_embedding_ready(*, strict: bool = False) -> bool:
        """Interpret the cached live embedding probe for health or strict init.

        This is a live signal, not a build-time one. A service object that
        was constructed at startup but whose provider now 404s (``bge-m3``
        never pulled, Ollama stopped) reports ``False`` here, so the popup's
        "semantic dedup off" banner reflects reality instead of going green
        while every embed silently fails. Conversely, once a previously
        broken provider is fixed the banner clears within the cache TTL.

        Layers:
          - no service object (provider not configured) -> ``False``;
          - service without a ``probe()`` (legacy/stub) -> build-only ``True``;
          - otherwise a cache-bypassing ``probe()``, result cached for
            ``_EMBEDDING_READY_TTL_SECONDS`` and single-flighted so concurrent
            polls share one provider round-trip. A loopback-Ollama timeout is
            optimistic only when ``strict`` is false; init always passes true.
        """
        nonlocal _embedding_probe_outcome, _embedding_ready_checked_at

        soul_engine = getattr(ctx, "soul_engine", None)
        service = getattr(soul_engine, "_embedding_service", None)
        if service is None:
            return False
        probe = getattr(service, "probe", None)
        if not callable(probe):
            # Legacy service without a live probe — "built" is the best signal.
            return True

        _embedding_ttl = _embedding_probe_ttl(_embedding_probe_outcome)
        if time.monotonic() - _embedding_ready_checked_at < _embedding_ttl:
            return _embedding_probe_result(_embedding_probe_outcome, strict=strict)

        async with _embedding_ready_lock:
            # Another request may have refreshed the cache while we waited.
            _embedding_ttl = _embedding_probe_ttl(_embedding_probe_outcome)
            if time.monotonic() - _embedding_ready_checked_at < _embedding_ttl:
                return _embedding_probe_result(_embedding_probe_outcome, strict=strict)
            try:
                ready = bool(
                    await asyncio.wait_for(probe(), timeout=_EMBEDDING_PROBE_TIMEOUT_SECONDS)
                )
                outcome: _EmbeddingProbeOutcome = "ready" if ready else "failed"
            except TimeoutError:
                logger.debug(
                    "Embedding readiness probe timed out; ordinary loopback-Ollama health "
                    "treats this as cold-loading while init remains strict"
                )
                outcome = "timed_out"
            except Exception:
                logger.debug("Embedding readiness probe errored", exc_info=True)
                outcome = "failed"
            _embedding_probe_outcome = outcome
            _embedding_ready_checked_at = time.monotonic()
            return _embedding_probe_result(outcome, strict=strict)

    def _embedding_required_for_init() -> bool:
        """Whether guided init must wait for a configured embedding provider."""
        cfg = getattr(ctx, "config", None)
        emb = getattr(getattr(cfg, "llm", None), "embedding", None)
        provider = str(getattr(emb, "provider", "") or "").strip()
        return bool(provider)

    @app.get("/api/ping")
    async def ping() -> JSONResponse:
        """Pure liveness probe: no DB, no provider round-trips.

        ``/api/health`` is a READINESS endpoint — its embedding probe can
        take seconds when the cache is cold (Ollama model reload), which
        made the extension's connection badge sit on "未连接" after opening
        the panel. UI liveness indicators should hit this instead and keep
        ``/api/health`` for profile/embedding state.
        """
        body: dict[str, object] = {"status": "ok", "service": "openbiliclaw-api"}
        if bool(getattr(ctx, "degraded", False)):
            # Preserve pure liveness semantics (status stays "ok") while
            # giving browser shells a provider-free fast path to avoid firing
            # every intentionally-blocked business request before /config
            # reveals the recovery state.
            body.update(
                {
                    "degraded": True,
                    "degraded_reason": str(getattr(ctx, "degraded_reason", "")),
                    "issues": _degraded_issues_payload(),
                }
            )
        return JSONResponse(body)

    @app.get("/api/qr-info")
    async def qr_info() -> JSONResponse:
        """Lightweight endpoint for mobile QR code: LAN IP only.

        Unlike ``/api/health``, this skips the embedding readiness probe
        so the QR drawer never blocks on a cold Ollama model load, and it
        detects the address fresh instead of serving the health TTL cache
        (see ``_fresh_lan_ip``) so a scanned code is never a network change
        behind.
        """
        return JSONResponse({"lan_ip": await _fresh_lan_ip()})

    @app.get(
        "/api/project-stats",
        response_model=ProjectStatsResponse,
        response_model_exclude_none=True,
    )
    async def project_stats() -> ProjectStatsResponse:
        """Return cached public project metadata without exposing GitHub failures."""
        snapshot = await project_stats_service.get_snapshot()
        return ProjectStatsResponse.model_validate(snapshot)

    def _health_llm_registered() -> bool:
        return not bool(getattr(ctx, "degraded", False))

    def _health_llm_callable() -> bool | None:
        """Best available signal for whether the default model chain works.

        A full live LLM probe on every /api/health poll would burn tokens, so
        this reads the persisted Codex OAuth capability probe written by
        ``openbiliclaw login codex --import`` / ``--probe``. For other
        providers no cheap live signal exists yet → ``None`` (unknown).
        """
        cfg = getattr(ctx, "config", None)
        if cfg is None:
            return None
        llm_cfg = getattr(cfg, "llm", None)
        if llm_cfg is None:
            return None
        codex_auth_mode = False
        if bool(getattr(llm_cfg, "instance_routing", False)):
            chain = list(getattr(llm_cfg, "default_chain", []) or [])
            instances = getattr(llm_cfg, "instances", {}) or {}
            if chain:
                instance = instances.get(str(chain[0]).strip().lower())
                if (
                    instance is not None
                    and str(getattr(instance, "provider_type", "") or "").strip().lower()
                    == "openai"
                ):
                    codex_auth_mode = (
                        str(getattr(instance, "auth_mode", "") or "").strip().lower()
                        == "codex_oauth"
                    )
        else:
            openai_cfg = getattr(llm_cfg, "openai", None)
            codex_auth_mode = bool(
                openai_cfg is not None
                and str(getattr(openai_cfg, "auth_mode", "") or "").strip().lower() == "codex_oauth"
            )
        if not codex_auth_mode:
            return None
        try:
            from openbiliclaw.llm.codex_auth import load_codex_credentials

            credentials = load_codex_credentials()
        except Exception:
            return False
        if credentials is None:
            return False
        probe = getattr(credentials, "last_probe", None)
        if probe is None:
            return None
        max_age = 7 * 24 * 3600
        if time.time() - float(getattr(probe, "checked_at", 0) or 0) > max_age:
            return None
        return bool(getattr(probe, "ok", False))

    @app.get("/api/health", response_model=HealthResponse, response_model_exclude_none=True)
    async def health() -> HealthResponse | JSONResponse:
        profile_ready = _health_profile_ready()
        lan_ip = _health_lan_ip()
        embedding_ready = await _health_embedding_ready()
        llm_registered = _health_llm_registered()
        llm_callable = _health_llm_callable() if llm_registered else False
        if bool(getattr(ctx, "degraded", False)) or llm_callable is False:
            body: dict[str, object] = {
                "status": "degraded",
                "service": "openbiliclaw-api",
                "embedding_ready": embedding_ready,
                "llm_registered": llm_registered,
                "llm_callable": llm_callable,
            }
            if bool(getattr(ctx, "degraded", False)):
                body["reason"] = str(getattr(ctx, "degraded_reason", ""))
                body["issues"] = _degraded_issues_payload()
            else:
                body["reason"] = (
                    "默认模型链已注册，但最近一次 Codex OAuth 能力探测失败："
                    "当前 ChatGPT/Codex 令牌无法用于 LLM 调用。请运行 "
                    "`openbiliclaw login codex --status --probe` 获取详情，"
                    "或改用 OpenAI Platform API Key。"
                )
            if profile_ready is not None:
                body["profile_ready"] = profile_ready
            if lan_ip is not None:
                body["lan_ip"] = lan_ip
            return JSONResponse(status_code=200, content=body)
        return HealthResponse(
            status="ok",
            service="openbiliclaw-api",
            profile_ready=profile_ready,
            lan_ip=lan_ip,
            embedding_ready=embedding_ready,
            llm_registered=llm_registered,
            llm_callable=llm_callable,
        )

    @app.get("/api/init-status", response_model=InitStatusOut)
    async def init_status(request: Request) -> InitStatusOut:
        """Authoritative guided-init status + pre-init checklist (gui-init §3).

        Remote-readable (mirrors autostart-status): a non-local caller still
        sees the state but ``can_manage`` is False. Degraded-mode readable.
        """
        from openbiliclaw.docker_runtime import is_running_in_container

        coord = ctx.init_coordinator
        prereqs = ctx.init_prereqs
        # A task can finish after its terminal DB write fails. Reconcile that
        # orphan before reporting status so retry/cancel is never blocked by a
        # logical ``running`` row with no owner.
        await coord.reconcile_orphaned_run()
        run = coord.get_status()
        initialized = bool(_health_profile_ready())
        running = bool(run["running"])
        terminal = run.get("status") in ("failed", "cancelled")
        embedding_required = _embedding_required_for_init()
        has_cached_readiness = getattr(prereqs, "has_cached_readiness", None)
        terminal_cache_ready = bool(
            terminal
            and callable(has_cached_readiness)
            and has_cached_readiness()
            and (not embedding_required or _embedding_ready_checked_at != float("-inf"))
        )
        if running:
            # Status polling is strictly observational while init owns the
            # expensive providers. Pre-flight already established readiness;
            # cached values are enough for a disabled checklist.
            bili = prereqs.peek_bilibili()
            chat = prereqs.peek_chat()
            embedding = _peek_embedding_ready(strict=True)
        elif initialized:
            # Steady state: once a profile exists the checklist is
            # informational only (can_start is false regardless, and POST
            # /api/init revalidates live before any force rebuild). Skip the
            # real chat/Bilibili probes so an open polling page — /setup/ or
            # the desktop web waiting for the first pool — no longer burns a
            # billable LLM ping per TTL window. Embedding is cache-only here
            # too: immediately after init, background prewarm can occupy its
            # provider semaphore for minutes, and a live status probe then
            # makes the completed page look frozen. /api/health remains the
            # dedicated live readiness surface; POST /api/init revalidates a
            # force rebuild before reserving a new run.
            bili = prereqs.peek_bilibili()
            chat = prereqs.peek_chat()
            embedding = _peek_embedding_ready(strict=True)
        elif terminal_cache_ready:
            # The just-finished run already passed a strict pre-flight. Return
            # its terminal state immediately so cancel/failure feedback is not
            # hidden behind a second 30s cold-model probe. After a process
            # restart these in-memory caches are absent and the live branch
            # below still refreshes the checklist normally.
            bili = prereqs.peek_bilibili()
            chat = prereqs.peek_chat()
            embedding = _peek_embedding_ready(strict=True)
        else:
            # Probe the three services concurrently — each is a real (now
            # strict) request with a generous cold-load timeout, so running
            # them sequentially could stack to ~40s. gather() bounds the wait
            # to the slowest single probe (TTL-cached, so steady-state polls
            # are instant).
            bili, chat, embedding = await asyncio.gather(
                prereqs.bilibili_check(),
                prereqs.chat_ready(),
                _health_embedding_ready(strict=True),
            )
        platforms = prereqs.enabled_platforms()
        # Project the exact same backend-owned capability contract exposed by
        # /api/sources/status.  This includes disabled sources because the setup
        # wizard can explicitly opt one in for the current run before config is
        # persisted; omitting it here would let a newly selected mixed-auth
        # source bypass the readiness gate.
        from openbiliclaw.api.source_auth.providers import (
            SOURCE_AUTH_PROVIDERS,
            SourceAuthContext,
        )

        capability_ctx = SourceAuthContext(
            cfg=ctx.config if ctx.config is not None else config,
            database=ctx.database,
        )
        source_capabilities = {
            slug: contract.capabilities
            for slug, provider in SOURCE_AUTH_PROVIDERS.items()
            if (contract := provider(capability_ctx)).capabilities
        }
        trusted = _get_auth_gate().is_trusted_local(request)
        supported = not is_running_in_container()
        # v0.3.118+: bilibili login is no longer a server-side hard gate —
        # whether it blocks depends on the client's per-run source selection,
        # which only POST /api/init sees. ``bilibili_logged_in`` stays in the
        # prerequisites payload so clients gate the start button themselves
        # when B站 is among the checked sources; POST revalidates regardless.
        hard_ok = chat and (embedding or not embedding_required)
        # Mirror POST /api/init's guards: an already-initialized profile blocks
        # a (non-force) start, so can_start must reflect that too — otherwise E1
        # and E2 disagree and a client could offer "start" that E2 rejects.
        can_start = trusted and supported and hard_ok and not running and not initialized

        # Account sync may be the first owner that tries to build preferences
        # after desktop startup. It persists a safe, user-facing failure, but
        # init-status historically never read it despite being the page's
        # authoritative source — so the UI still sat at 49% with no reason.
        account_profile_error = ""
        if not initialized and not running:
            sync_status = getattr(ctx.account_sync_service, "get_runtime_status", None)
            if callable(sync_status):
                with suppress(Exception):
                    raw_status = sync_status()
                    if isinstance(raw_status, dict):
                        candidate = str(raw_status.get("last_account_sync_error", "")).strip()
                        if candidate.startswith("画像分析失败："):
                            account_profile_error = candidate[:500]

        embedding_check, embedding_detail = (
            _peek_embedding_diagnosis(bool(embedding))
            if running or terminal_cache_ready or initialized
            else await _diagnose_embedding(bool(embedding))
        )
        pull_progress = _embedding_pull_progress_view()
        pull_status = str(pull_progress.get("status_text") or "")

        if not supported:
            reason, detail = "unsupported_runtime", "Docker 运行时不支持图形化初始化"
        elif _init_blocked_by_degraded():
            # LLM registry never built → init is impossible until config is
            # repaired. Surface the same actionable cause as POST /api/init so
            # the checklist doesn't read as a generic "AI 服务不可用".
            reason, detail = "degraded", _degraded_init_detail
        elif running:
            reason, detail = "already_running", "初始化进行中"
        elif initialized:
            if bool(run["partial_success"]):
                # Preserve the terminal cause written by complete() so setup /
                # desktop / popup can distinguish discovery degradation from a
                # partial source import instead of collapsing both into the old
                # "stuck at 95%" explanation.
                reason = str(run.get("reason") or "discovery_partial")
                detail = str(
                    run.get("detail")
                    or "画像已生成，但首轮内容池本次未完成；系统会在后台继续补齐。"
                )
            else:
                reason, detail = "already_initialized", "已经初始化过了；如需重建请用 force"
        elif not trusted:
            # trusted participates in can_start but had no reason branch, so
            # remote/paired-mobile viewers got can_start=false with
            # reason="none" — every client fell back to a generic "条件未满足"
            # while the checklist showed all-green (field report 2026-07-05).
            # All clients already map local_only to "只能在本机发起初始化。".
            reason, detail = "local_only", "只能在本机发起初始化"
        elif run.get("status") in ("failed", "cancelled"):
            # The terminal run is the authoritative explanation of what just
            # happened.  A follow-up readiness probe can independently fail
            # (for example a cold local model timing out), but must not replace
            # a precise persisted "analysis exceeded six minutes" error with
            # the generic "AI service unavailable" banner.
            reason = run.get("reason") or str(run.get("status"))
            detail = str(run.get("detail") or "")
        elif not chat:
            reason = "llm_not_ready"
            # Prefer the classified probe cause (无效 API Key / 服务不可达 /
            # 模型不存在) so the checklist explains WHY chat is down; a stored
            # account-analysis failure still wins as the more specific history.
            detail = (
                account_profile_error or prereqs.peek_chat_detail() or "AI 服务还没配好或当前不可用"
            )
        elif embedding_required and not embedding:
            reason, detail = "embedding_not_ready", "向量模型还没就绪"
        elif bili != "ok":
            # Informational (does not flip can_start): blocks only if the
            # client keeps bilibili selected, which the UI enforces.
            reason, detail = "bilibili_not_logged_in", "还没检测到 B站 登录"
        elif account_profile_error:
            # The current probe is healthy again, so retry is allowed, but the
            # previous background analysis failure still explains why no
            # profile exists yet.
            reason, detail = "analyze_failed", account_profile_error
        else:
            reason, detail = "none", ""

        return InitStatusOut(
            initialized=initialized,
            running=running,
            run_id=run["run_id"],
            sequence=run["sequence"],
            current_stage=run["current_stage"],
            total_stages=run["total_stages"],
            stages=[InitStageOut(**s) for s in run["stages"]],
            partial_success=bool(run["partial_success"]),
            can_start=can_start,
            can_manage=trusted,
            prerequisites=InitPrerequisitesOut(
                bilibili_logged_in=(bili == "ok"),
                bilibili_check=bili,
                bilibili_detail=prereqs.peek_bilibili_detail() if bili == "failed" else "",
                llm_ready=chat,
                embedding_ready=embedding,
                embedding_check=embedding_check,
                embedding_detail=embedding_detail,
                embedding_repair_running=bool(pull_progress["running"]),
                embedding_repair_completed=_progress_int(pull_progress.get("completed")),
                embedding_repair_total=_progress_int(pull_progress.get("total")),
                ollama_phase=embedding_progress.ollama_phase(),
                embedding_pull_status=pull_status,
                embedding_required=embedding_required,
                enabled_platforms=platforms,
                source_capabilities=source_capabilities,
            ),
            reason=reason,
            detail=detail,
            last_activity=str(run.get("last_activity") or ""),
            last_heartbeat_at=str(run.get("last_heartbeat_at") or ""),
            last_progress_at=str(run.get("last_progress_at") or ""),
            progress_sequence=int(run.get("progress_sequence") or 0),
        )

    def _init_runtime_supported() -> tuple[bool, str]:
        """Cheap guard: GUI init needs a writable host runtime (gui-init §5b,
        review R2 A-7). Docker uses the headless auto-init path instead."""
        from openbiliclaw.docker_runtime import is_running_in_container

        if is_running_in_container():
            return False, "Docker 运行时不支持图形化初始化"
        cfg = ctx.config
        if cfg is not None:
            try:
                if not os.access(str(cfg.data_path), os.W_OK):
                    return False, "数据目录不可写"
            except Exception:
                pass
        return True, ""

    async def _persist_guided_init_source_opt_in(
        effective_sources: set[str],
        *,
        bangumi_username: str | None = None,
        bangumi_token: str | None = None,
        github_username: str | None = None,
        github_token: str | None = None,
        v2ex_username: str | None = None,
    ) -> bool:
        """Best-effort: checked guided-init sources become enabled settings.

        The run itself uses ``effective_sources`` directly, so a config write
        failure must not block initialization. Persisting keeps the setup page,
        popup, and later background discovery aligned with the user's explicit
        checkbox choice.
        """

        cfg = getattr(ctx, "config", None)
        sources_cfg = getattr(cfg, "sources", None) if cfg is not None else None
        if sources_cfg is None or not effective_sources:
            return False

        changed = False
        for source in _INIT_SOURCE_ORDER:
            if source not in effective_sources:
                continue
            source_cfg = getattr(sources_cfg, source, None)
            if source_cfg is not None and not bool(getattr(source_cfg, "enabled", False)):
                source_cfg.enabled = True
                changed = True
        bangumi_cfg = getattr(sources_cfg, "bangumi", None)
        if (
            bangumi_cfg is not None
            and "bangumi" in effective_sources
            and bangumi_username is not None
            and str(getattr(bangumi_cfg, "username", "") or "").strip() != bangumi_username
        ):
            bangumi_cfg.username = bangumi_username
            changed = True
        github_cfg = getattr(sources_cfg, "github", None)
        if (
            github_cfg is not None
            and "github" in effective_sources
            and github_username is not None
            and str(getattr(github_cfg, "username", "") or "").strip() != github_username
        ):
            github_cfg.username = github_username
            changed = True
        v2ex_cfg = getattr(sources_cfg, "v2ex", None)
        if (
            v2ex_cfg is not None
            and "v2ex" in effective_sources
            and v2ex_username is not None
            and str(getattr(v2ex_cfg, "username", "") or "").strip() != v2ex_username
        ):
            v2ex_cfg.username = v2ex_username
            changed = True
        # Persist the validated token so background discovery and later syncs
        # authenticate without re-prompting (rule 7: only after /v0/me passed).
        if (
            bangumi_cfg is not None
            and "bangumi" in effective_sources
            and bangumi_token is not None
            and str(getattr(bangumi_cfg, "access_token", "") or "").strip() != bangumi_token
        ):
            bangumi_cfg.access_token = bangumi_token
            changed = True
        if (
            github_cfg is not None
            and "github" in effective_sources
            and github_token is not None
            and str(getattr(github_cfg, "access_token", "") or "").strip() != github_token
        ):
            github_cfg.access_token = github_token
            changed = True
        if not changed:
            return False

        try:
            from openbiliclaw.config import Config, save_config

            cfg = cast("Config", cfg)

            async with _CONFIG_SAVE_LOCK:
                save_config(_preserve_persisted_restart_fields(cfg))
        except Exception:
            logger.warning("guided init source opt-in save_config failed", exc_info=True)
            return False

        if bool(getattr(ctx, "degraded", False)):
            return False
        try:
            await _rebuild_runtime_with_lane_handoff(
                cfg,
                run_post_reload_llm_work=False,
                resume_execution_lanes=False,
            )
            with suppress(Exception):
                await ctx.event_hub.publish(
                    {
                        "type": "config_reloaded",
                        "message": "初始化来源选择已写入配置。",
                    }
                )
            return True
        except Exception:
            logger.warning("guided init source opt-in hot-reload failed", exc_info=True)
            # The handoff attempts a suspended recovery in its own exception
            # path, so callers must still restore normal owners if init aborts.
            return True

    async def _run_guided_init_wrapper(
        run_id: str,
        selected_sources: set[str] | None = None,
        bangumi_username: str = "",
        bangumi_token: str = "",
        github_username: str = "",
        github_token: str = "",
        v2ex_username: str = "",
        force: bool = False,
        reset_cognition: bool = False,
        llm_concurrency: int | None = None,
    ) -> None:
        """Sole status/event writer for an API-launched guided init (gui-init
        §5f). Drives the shared ``run_guided_init`` through the coordinator and
        persists the terminal state here — completed / failed / cancelled —
        never via a side path. Imported lazily to avoid an import cycle with
        the CLI module that owns the shared pipeline.

        ``selected_sources`` is the extension's per-run platform choice. When
        present, it is an explicit local opt-in for those sources (see
        :func:`_select_init_platforms`). ``None`` keeps the legacy behaviour of
        using everything enabled.
        """
        from openbiliclaw.cli import (
            _INIT_BILIBILI_FAVORITE_LIMIT,
            _INIT_BILIBILI_FOLLOW_LIMIT,
            _INIT_POOL_TARGET_COUNT,
            GuidedInitError,
            run_guided_init,
        )

        coord = ctx.init_coordinator

        def _purge_pool_for_reinit() -> int:
            # Force re-init: retire every active pool row so the fresh profile
            # immediately yields new recommendations. Called by the shared
            # pipeline at stage-4 start (unconditionally, not via backfill
            # signature sniffing — a backfill that omits the flag would
            # otherwise silently skip the purge, see field report 2026-08-12).
            return int(ctx.database.mark_pool_purged_by_reinit() or 0)

        reinit_backup_dir: str | None = None
        if force:
            # Snapshot DB + memory layers before the rebuild so nothing the
            # re-init replaces or deletes (soul profile, and with
            # reset_cognition the awareness/insight layers) is unrecoverable.
            try:
                from openbiliclaw.cli import _create_reinit_backup

                backup_path = _create_reinit_backup()
                if backup_path is not None:
                    reinit_backup_dir = str(backup_path)
                    logger.info("re-init backup created at %s", reinit_backup_dir)
            except Exception:
                logger.warning("re-init backup failed in wrapper", exc_info=True)

        async def _api_discover_backfill(
            profile: Any,
            *,
            target_pool_count: int,
            label_suffix: str = "",
            progress_callback: Any = None,
        ) -> int:
            # API path backfills through the live controller so it holds the
            # refresh lock (B1); ``label_suffix`` is CLI-only console flavour.
            return int(
                await ctx.runtime_controller.run_init_backfill(
                    profile,
                    target_pool_count,
                    fully_parallel=True,
                    progress_callback=progress_callback,
                )
            )

        heartbeat_task: asyncio.Task[None] | None = None
        try:
            await coord.mark_running(run_id)
            # Liveness heartbeat: even if a single stage-2 LLM call blocks for
            # minutes, last_activity stays ≤30s fresh so the GUI never falsely
            # reads "stalled" (init-progress spec Phase 0). Torn down in finally.
            heartbeat_task = asyncio.create_task(_run_init_heartbeat(coord, run_id))
            enabled = set(ctx.init_prereqs.enabled_platforms())
            effective = _select_init_platforms(enabled, selected_sources)
            result = await run_guided_init(
                client=ctx.bilibili_client,
                memory=ctx.memory_manager,
                soul_engine=ctx.soul_engine,
                favorite_limit=_INIT_BILIBILI_FAVORITE_LIMIT,
                follow_limit=_INIT_BILIBILI_FOLLOW_LIMIT,
                include_bili="bilibili" in effective,
                include_xhs="xiaohongshu" in effective,
                include_dy="douyin" in effective,
                include_yt="youtube" in effective,
                include_x="twitter" in effective,
                include_zhihu="zhihu" in effective,
                include_reddit="reddit" in effective,
                include_v2ex="v2ex" in effective,
                include_bangumi="bangumi" in effective,
                include_github="github" in effective,
                include_linuxdo="linuxdo" in effective,
                include_weibo="weibo" in effective,
                bangumi_username=bangumi_username,
                bangumi_token=bangumi_token,
                github_username=github_username,
                github_token=github_token,
                v2ex_username=v2ex_username,
                target_pool_count=_INIT_POOL_TARGET_COUNT,
                discover_backfill=_api_discover_backfill,
                coordinator=coord,
                run_id=run_id,
                # Force re-init retires the old recommendation pool so the
                # fresh profile immediately yields new recommendations.
                purge_pool_callback=_purge_pool_for_reinit if force else None,
                # Optional: clear old awareness/insight observations (e.g.
                # from a previous account) before the new profile build.
                reset_cognition=reset_cognition,
                llm_concurrency=llm_concurrency,
            )
            discovery_partial = bool(result.discovery_error)
            dy_status = str(getattr(result, "dy_status", "skipped") or "skipped")
            dy_degraded = dy_status == "degraded"
            linuxdo_status = str(getattr(result, "linuxdo_status", "skipped") or "skipped")
            linuxdo_degraded = linuxdo_status == "degraded"
            weibo_status = str(getattr(result, "weibo_status", "skipped") or "skipped")
            weibo_degraded = weibo_status in {"failed", "timeout", "login_required"}
            partial_success = discovery_partial or dy_degraded or linuxdo_degraded or weibo_degraded
            v2ex_status = str(getattr(result, "v2ex_status", "skipped") or "skipped")
            v2ex_partial = v2ex_status == "partial"
            github_status = str(getattr(result, "github_status", "skipped") or "skipped")
            github_partial = github_status == "partial"
            partial_success = (
                discovery_partial
                or dy_degraded
                or linuxdo_degraded
                or v2ex_partial
                or github_partial
                or weibo_degraded
            )
            reason = getattr(result, "discovery_reason", None)
            detail = str(getattr(result, "discovery_detail", "") or "").strip()
            if dy_degraded:
                dy_event_count = len(getattr(result, "dy_events", []) or [])
                dy_detail = (
                    "抖音采集状态 dy_status=degraded："
                    f"已保留并用于画像建模 {dy_event_count} 条已采事件，"
                    "但至少一个范围未能证明分页完整。"
                )
                detail = " ".join(part for part in (detail, dy_detail) if part)
                if not discovery_partial:
                    reason = "douyin_degraded"
            if linuxdo_degraded:
                linuxdo_event_count = len(getattr(result, "linuxdo_events", []) or [])
                linuxdo_detail = (
                    "Linux.do 采集状态 degraded："
                    f"已保留并用于画像建模 {linuxdo_event_count} 条已采事件，"
                    "但至少一个个人范围未完成。"
                )
                detail = " ".join(part for part in (detail, linuxdo_detail) if part)
                if not discovery_partial and not dy_degraded:
                    reason = "linuxdo_degraded"
            if v2ex_partial:
                v2ex_event_count = len(getattr(result, "v2ex_events", []) or [])
                v2ex_detail = (
                    "V2EX 采集状态 v2ex_status=partial："
                    f"已保留并用于画像建模 {v2ex_event_count} 条事件，"
                    "但至少一个公开或登录态范围未完整返回。"
                )
                detail = " ".join(part for part in (detail, v2ex_detail) if part)
                if not discovery_partial and not dy_degraded:
                    reason = "v2ex_partial"
            if github_partial:
                github_event_count = len(getattr(result, "github_events", []) or [])
                github_detail = (
                    "GitHub 采集状态 github_status=partial："
                    f"已保留并用于画像建模 {github_event_count} 条公开 starred repository 事件，"
                    "但分页、结果上限或上游响应未能证明完整。"
                )
                detail = " ".join(part for part in (detail, github_detail) if part)
                if not discovery_partial and not dy_degraded and not v2ex_partial:
                    reason = "github_partial"
            if weibo_degraded:
                weibo_event_count = len(getattr(result, "weibo_events", []) or [])
                weibo_detail = (
                    f"微博采集状态 weibo_status={weibo_status}："
                    f"已保留 {weibo_event_count} 条已采事件；"
                    "请确认当前浏览器登录态和扩展连接后重试。"
                )
                detail = " ".join(part for part in (detail, weibo_detail) if part)
                if (
                    not discovery_partial
                    and not dy_degraded
                    and not v2ex_partial
                    and not github_partial
                ):
                    reason = "weibo_degraded"
            await coord.complete(
                run_id,
                partial_success=partial_success,
                reason=reason,
                detail=detail or None,
            )
        except asyncio.CancelledError:
            # Cancel was requested via /api/init/cancel — shield the terminal
            # write so the cancelled status still lands before we propagate.
            with suppress(Exception):
                await asyncio.shield(coord.cancel(run_id))
            raise
        except GuidedInitError as exc:
            logger.warning("guided init %s failed: %s", run_id, exc.reason)
            with suppress(Exception):
                await coord.fail(run_id, exc.reason, detail=exc.message)
        except Exception as exc:
            logger.exception("guided init %s crashed", run_id)
            with suppress(Exception):
                await coord.fail(run_id, "internal_error", detail=_init_crash_detail(exc))
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(BaseException):
                    await heartbeat_task
            if not bool(getattr(ctx, "degraded", False)):
                with suppress(Exception):
                    await _restart_background_tasks_after_event_recovery()

    @app.post("/api/init")
    async def start_guided_init(request: Request) -> JSONResponse:
        """Launch guided init in the background (local-only; gui-init §2/§5b).

        Cheap rejections run BEFORE reserving the run so a rejected request
        never leaves a stuck ``starting`` row (review R2 A-2). The single-flight
        guard is the DB reservation inside ``try_start``.
        """
        if not _get_auth_gate().is_trusted_local(request):
            return JSONResponse({"error": "local_only"}, status_code=403)
        # Degraded mode reaches this handler (the guard allow-lists /api/init so
        # the recovery UI stays live), but no LLM registry means init cannot run.
        # Reject with an actionable cause instead of crashing on a missing
        # coordinator or bubbling a bare error (project rule 7).
        if _init_blocked_by_degraded():
            return JSONResponse(
                {"error": "degraded", "detail": _degraded_init_detail},
                status_code=409,
            )
        if _config_apply_busy():
            return JSONResponse(
                {
                    "error": "config_applying",
                    "detail": "配置正在后台应用，请等待热重载完成后再开始初始化。",
                },
                status_code=409,
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        force = bool(body.get("force", False)) if isinstance(body, dict) else False
        # Re-init option: clear the long-term awareness / insight layers before
        # the rebuild so old observations (e.g. from a previous account) do not
        # leak into the new profile. Only meaningful with ``force``.
        reset_cognition = (
            bool(body.get("reset_cognition", False)) if isinstance(body, dict) else False
        )
        # Optional per-run LLM concurrency for the preference-analysis stage.
        # The init page sends an explicit value (default 4 in the UI); older
        # clients omit it and the backend keeps the configured llm.concurrency.
        raw_llm_concurrency = body.get("llm_concurrency") if isinstance(body, dict) else None
        if raw_llm_concurrency is None:
            llm_concurrency: int | None = None
        else:
            try:
                llm_concurrency = int(raw_llm_concurrency)
            except (TypeError, ValueError):
                return JSONResponse(
                    {
                        "error": "invalid_llm_concurrency",
                        "detail": "llm_concurrency 必须是 1-16 的整数",
                    },
                    status_code=400,
                )
            if not (1 <= llm_concurrency <= 16):
                return JSONResponse(
                    {
                        "error": "invalid_llm_concurrency",
                        "detail": "llm_concurrency 必须是 1-16 的整数",
                    },
                    status_code=400,
                )
        # Optional per-run platform selection from the extension checkboxes. A
        # list (even empty) is an explicit choice; absent → None = use all
        # enabled (CLI / legacy clients). Sent source keys are explicit opt-ins
        # for this local guided-init run.
        raw_sources = body.get("sources") if isinstance(body, dict) else None
        selected_sources = {str(s) for s in raw_sources} if isinstance(raw_sources, list) else None
        source_options = body.get("source_options") if isinstance(body, dict) else None
        if source_options is not None and not isinstance(source_options, dict):
            return JSONResponse(
                {"error": "invalid_source_options", "detail": "source_options 必须是对象"},
                status_code=400,
            )
        source_options = source_options or {}
        unknown_source_options = sorted(set(source_options) - {"bangumi", "github", "v2ex"})
        if unknown_source_options:
            return JSONResponse(
                {
                    "error": "invalid_source_options",
                    "detail": f"不支持的 source_options: {', '.join(unknown_source_options)}",
                },
                status_code=400,
            )
        bangumi_options = source_options.get("bangumi", {})
        if not isinstance(bangumi_options, dict):
            return JSONResponse(
                {
                    "error": "invalid_source_options",
                    "detail": "source_options.bangumi 必须是对象",
                },
                status_code=400,
            )
        unknown_bangumi_options = sorted(set(bangumi_options) - {"username", "access_token"})
        if unknown_bangumi_options:
            return JSONResponse(
                {
                    "error": "invalid_source_options",
                    "detail": (
                        "不支持的 source_options.bangumi 字段: "
                        + ", ".join(unknown_bangumi_options)
                    ),
                },
                status_code=400,
            )
        scoped_bangumi_username = "username" in bangumi_options
        legacy_bangumi_username = isinstance(body, dict) and "bangumi_username" in body
        bangumi_username_supplied = scoped_bangumi_username or legacy_bangumi_username
        selected_bangumi_username: str | None = None
        if bangumi_username_supplied:
            from openbiliclaw.sources.bangumi_client import validate_bangumi_username

            try:
                raw_bangumi_username = (
                    bangumi_options.get("username")
                    if scoped_bangumi_username
                    else body.get("bangumi_username")
                )
                selected_bangumi_username = validate_bangumi_username(raw_bangumi_username)
            except ValueError as exc:
                return JSONResponse(
                    {"error": "invalid_bangumi_username", "detail": str(exc)},
                    status_code=400,
                )
        scoped_bangumi_token = "access_token" in bangumi_options
        selected_bangumi_token: str | None = None
        if scoped_bangumi_token:
            from openbiliclaw.sources.bangumi_client import validate_bangumi_access_token

            try:
                selected_bangumi_token = validate_bangumi_access_token(
                    bangumi_options.get("access_token")
                )
            except ValueError as exc:
                return JSONResponse(
                    {"error": "invalid_bangumi_access_token", "detail": str(exc)},
                    status_code=400,
                )

        github_options = source_options.get("github", {})
        if not isinstance(github_options, dict):
            return JSONResponse(
                {"error": "invalid_source_options", "detail": "source_options.github 必须是对象"},
                status_code=400,
            )
        unknown_github_options = sorted(set(github_options) - {"username", "access_token"})
        if unknown_github_options:
            return JSONResponse(
                {
                    "error": "invalid_source_options",
                    "detail": (
                        "不支持的 source_options.github 字段: " + ", ".join(unknown_github_options)
                    ),
                },
                status_code=400,
            )
        scoped_github_username = "username" in github_options
        selected_github_username: str | None = None
        if scoped_github_username:
            from openbiliclaw.sources.github_client import validate_github_username

            try:
                selected_github_username = validate_github_username(github_options.get("username"))
            except ValueError as exc:
                return JSONResponse(
                    {"error": "invalid_github_username", "detail": str(exc)},
                    status_code=400,
                )
        scoped_github_token = "access_token" in github_options
        selected_github_token: str | None = None
        if scoped_github_token:
            from openbiliclaw.sources.github_client import validate_github_access_token

            try:
                selected_github_token = validate_github_access_token(
                    github_options.get("access_token")
                )
            except ValueError as exc:
                return JSONResponse(
                    {"error": "invalid_github_access_token", "detail": str(exc)},
                    status_code=400,
                )

        v2ex_options = source_options.get("v2ex", {})
        if not isinstance(v2ex_options, dict):
            return JSONResponse(
                {"error": "invalid_source_options", "detail": "source_options.v2ex 必须是对象"},
                status_code=400,
            )
        unknown_v2ex_options = sorted(set(v2ex_options) - {"username"})
        if unknown_v2ex_options:
            return JSONResponse(
                {
                    "error": "invalid_source_options",
                    "detail": (
                        "不支持的 source_options.v2ex 字段: " + ", ".join(unknown_v2ex_options)
                    ),
                },
                status_code=400,
            )
        scoped_v2ex_username = "username" in v2ex_options
        legacy_v2ex_username = isinstance(body, dict) and "v2ex_username" in body
        v2ex_username_supplied = scoped_v2ex_username or legacy_v2ex_username
        selected_v2ex_username: str | None = None
        if v2ex_username_supplied:
            from openbiliclaw.sources.v2ex_client import validate_v2ex_username

            try:
                raw_v2ex_username = (
                    v2ex_options.get("username")
                    if scoped_v2ex_username
                    else body.get("v2ex_username")
                )
                selected_v2ex_username = validate_v2ex_username(raw_v2ex_username)
            except ValueError as exc:
                return JSONResponse(
                    {"error": "invalid_v2ex_username", "detail": str(exc)},
                    status_code=400,
                )

        coord = ctx.init_coordinator

        # Release a previous ownerless active row before source persistence or
        # the single-flight CAS. Fresh ``starting`` rows retain their 120s
        # lease, so concurrent starts are rejected rather than stolen.
        await coord.reconcile_orphaned_run()

        supported, detail = _init_runtime_supported()
        if not supported:
            return JSONResponse({"error": "unsupported_runtime", "detail": detail}, status_code=409)
        if not force and _health_profile_ready() is True:
            return JSONResponse(
                {"error": "already_initialized", "detail": "已初始化；重建请传 force"},
                status_code=409,
            )
        # At least one valid source must survive an EXPLICIT per-run selection
        # (v0.3.118+: bilibili is selectable like the rest, so an empty
        # selection is reachable). Cheap rejection, before reserving. Legacy
        # clients (no "sources" key) stay permissive.
        effective_sources = _select_init_platforms(
            set(ctx.init_prereqs.enabled_platforms()), selected_sources
        )
        if selected_sources is not None and not effective_sources:
            return JSONResponse(
                {"error": "no_sources_selected", "detail": "至少选择一个有效数据来源"},
                status_code=409,
            )
        # Preserve the explicit opt-in independently from this run's profile
        # capability admission. A signed-out optional source may stay enabled
        # for public discovery, but it must not be scheduled as a personal
        # profile source until its capability-specific prerequisite is ready.
        sources_to_persist = set(effective_sources)
        warnings: list[str] = []
        capability_readiness = getattr(
            ctx.init_prereqs,
            "source_capability_readiness",
            None,
        )
        linuxdo_capability_readiness = capability_readiness
        if "linuxdo" in effective_sources and callable(linuxdo_capability_readiness):
            linuxdo_profile_readiness = str(
                linuxdo_capability_readiness("linuxdo", "profile") or "unverified"
            )
            if linuxdo_profile_readiness != "ready":
                if effective_sources == {"linuxdo"}:
                    return JSONResponse(
                        {
                            "error": "no_profile_signal_sources",
                            "detail": (
                                "Linux.do 公开发现无需登录，但初始化所需的"
                                "收藏、点赞和阅读记录需要已登录的浏览器会话。"
                                "请先在当前浏览器登录 Linux.do 并连接插件。"
                            ),
                            "capability": "profile",
                            "readiness": linuxdo_profile_readiness,
                        },
                        status_code=409,
                    )
                effective_sources.discard("linuxdo")
                warnings.append(
                    "Linux.do 个人信号未就绪：本次初始化跳过收藏、"
                    "点赞和阅读记录；公开发现保持启用。"
                )
        weibo_capability_readiness = capability_readiness
        if "weibo" in effective_sources and callable(weibo_capability_readiness):
            weibo_profile_readiness = str(
                weibo_capability_readiness("weibo", "profile") or "unverified"
            )
            if weibo_profile_readiness != "ready":
                if effective_sources == {"weibo"}:
                    return JSONResponse(
                        {
                            "error": "no_profile_signal_sources",
                            "detail": (
                                "微博公开发现无需登录，但初始化本人收藏、关注和互动记录"
                                "需要当前浏览器已登录微博并连接扩展。请先登录 weibo.com 或"
                                "m.weibo.cn 后重试。"
                            ),
                            "capability": "profile",
                            "readiness": weibo_profile_readiness,
                        },
                        status_code=409,
                    )
                effective_sources.discard("weibo")
                warnings.append(
                    "微博个人信号未就绪：本次初始化跳过收藏、关注和互动记录；公开发现保持启用。"
                )
        configured_bangumi_username = str(
            getattr(
                getattr(
                    getattr(getattr(ctx, "config", None), "sources", None),
                    "bangumi",
                    None,
                ),
                "username",
                "",
            )
            or ""
        ).strip()
        effective_bangumi_username = (
            configured_bangumi_username
            if selected_bangumi_username is None
            else selected_bangumi_username
        )
        configured_bangumi_token = str(
            getattr(
                getattr(
                    getattr(getattr(ctx, "config", None), "sources", None),
                    "bangumi",
                    None,
                ),
                "access_token",
                "",
            )
            or ""
        ).strip()
        effective_bangumi_token = (
            configured_bangumi_token if selected_bangumi_token is None else selected_bangumi_token
        )
        init_runtime_config = ctx.config if ctx.config is not None else config
        github_cfg = getattr(getattr(init_runtime_config, "sources", None), "github", None)
        configured_github_username = str(getattr(github_cfg, "username", "") or "").strip()
        effective_github_username = (
            configured_github_username
            if selected_github_username is None
            else selected_github_username
        )
        configured_github_token = str(getattr(github_cfg, "access_token", "") or "").strip()
        github_token_input = (
            configured_github_token if selected_github_token is None else selected_github_token
        )
        from openbiliclaw.sources.github_client import resolve_github_access_token

        effective_github_token, _github_token_origin = resolve_github_access_token(
            github_token_input
        )
        configured_v2ex_username = str(
            getattr(
                getattr(getattr(init_runtime_config, "sources", None), "v2ex", None),
                "username",
                "",
            )
            or ""
        ).strip()
        effective_v2ex_username = (
            configured_v2ex_username if selected_v2ex_username is None else selected_v2ex_username
        )
        if "v2ex" in effective_sources:
            from openbiliclaw.api.source_auth.providers import (
                SourceAuthContext,
                v2ex_capability_readiness,
            )

            v2ex_readiness = v2ex_capability_readiness(
                SourceAuthContext(cfg=init_runtime_config, database=ctx.database),
                configured_username=effective_v2ex_username,
            )["bootstrap"]
            if not v2ex_readiness.ready:
                return JSONResponse(
                    {
                        "error": "v2ex_bootstrap_not_ready",
                        "detail": v2ex_readiness.detail,
                        "capability": v2ex_readiness.model_dump(),
                    },
                    status_code=409,
                )
        if "github" in effective_sources:
            if not effective_github_username and not effective_github_token:
                if effective_sources == {"github"}:
                    return JSONResponse(
                        {
                            "error": "no_profile_signal_sources",
                            "detail": (
                                "GitHub 公开仓库发现无需登录，但初始化画像需要公开用户名"
                                "或可用 PAT，以只读导入该账号的公开 starred repositories。"
                            ),
                            "capability": "profile",
                            "readiness": "identity_required",
                        },
                        status_code=409,
                    )
                effective_sources.discard("github")
                warnings.append(
                    "GitHub 未填写公开用户名或 PAT：本次初始化跳过 starred repositories；"
                    "公开仓库发现保持启用。"
                )
            else:
                from openbiliclaw.sources.github_client import (
                    GitHubAPIError,
                    GitHubClient,
                    resolve_github_bootstrap_identity,
                )

                try:
                    async with GitHubClient(
                        token=effective_github_token,
                        request_interval_seconds=0.1,
                    ) as github_client:
                        github_identity = await resolve_github_bootstrap_identity(
                            github_client,
                            username=effective_github_username,
                        )
                except GitHubAPIError as exc:
                    if exc.code == "unauthorized":
                        error_code = "invalid_github_access_token"
                        error_detail = (
                            "GitHub PAT 被拒绝（缺失、错误或已过期）。"
                            "请更换令牌，或清除 PAT 后使用公开用户名。"
                        )
                        error_status = 400
                    elif exc.code == "identity_mismatch":
                        error_code = "github_identity_mismatch"
                        error_detail = "GitHub PAT 所属账号与填写的公开用户名不一致。"
                        error_status = 409
                    elif exc.code == "not_found":
                        error_code = "github_bootstrap_not_ready"
                        error_detail = "GitHub 公开用户名不存在或当前不可访问。"
                        error_status = 400
                    else:
                        error_code = "github_token_check_failed"
                        error_detail = str(exc)
                        error_status = 502
                    if effective_sources == {"github"}:
                        return JSONResponse(
                            {"error": error_code, "detail": error_detail},
                            status_code=error_status,
                        )
                    effective_sources.discard("github")
                    warnings.append(
                        f"GitHub 画像初始化已隔离（{error_code}）：{error_detail} "
                        "其他来源继续初始化；公开仓库发现保持启用。"
                    )
                    # Never persist request-scoped identity input that failed
                    # its official read-only preflight.
                    selected_github_username = None
                    selected_github_token = None
                    effective_github_username = ""
                    effective_github_token = ""
                else:
                    effective_github_username = github_identity.login
        # A personal access token identifies the account via /v0/me, so validate
        # it live and resolve the username BEFORE reserving a run or persisting —
        # reject a bad/expired token with its real cause (project rule 7) instead
        # of writing an unusable secret.
        if "bangumi" in effective_sources and effective_bangumi_token:
            from openbiliclaw.sources.bangumi_client import (
                BangumiAPIError,
                resolve_access_token_identity,
            )

            try:
                resolved_bangumi_username = await resolve_access_token_identity(
                    effective_bangumi_token
                )
            except BangumiAPIError as exc:
                if exc.code == "unauthorized":
                    return JSONResponse(
                        {
                            "error": "invalid_bangumi_access_token",
                            "detail": (
                                "Bangumi 个人令牌被拒绝（缺失、错误或已过期）。请到 "
                                "https://next.bgm.tv/demo/access-token 重新生成后重试。"
                            ),
                        },
                        status_code=400,
                    )
                return JSONResponse(
                    {"error": "bangumi_token_check_failed", "detail": str(exc)},
                    status_code=502,
                )
            if (
                effective_bangumi_username
                and effective_bangumi_username != resolved_bangumi_username
            ):
                warnings.append(
                    f"Bangumi 令牌对应用户为 {resolved_bangumi_username}，已覆盖填写的用户名。"
                )
            effective_bangumi_username = resolved_bangumi_username
        # Zero-config fallback: the content script on bgm.tv reports the
        # logged-in account's public uid + username. Priority ladder:
        # token /v0/me > explicit/configured username > extension-reported
        # username > reject. Never overrides an explicit choice.
        if (
            "bangumi" in effective_sources
            and not effective_bangumi_username
            and not effective_bangumi_token
        ):
            identity_loader = getattr(app.state, "load_bangumi_identity", None)
            extension_bangumi_username, extension_identity_verified = (
                identity_loader() if callable(identity_loader) else ("", False)
            )
            if extension_bangumi_username:
                effective_bangumi_username = extension_bangumi_username
                # Say which one it is. An unverified identity is a best-effort
                # DOM read that bgm.tv never confirmed, so the user has to be
                # able to catch a wrong account instead of trusting the same
                # confident sentence for both cases.
                warnings.append(
                    f"Bangumi 使用浏览器扩展识别到的账号 {extension_bangumi_username}。"
                    if extension_identity_verified
                    else (
                        f"Bangumi 使用浏览器扩展识别到的账号 {extension_bangumi_username}"
                        "（未经 bgm.tv 校验，可能不准）。如果不是你本人，请填写公开用户名"
                        "或个人令牌后重新初始化。"
                    )
                )
        if (
            effective_sources == {"bangumi"}
            and not effective_bangumi_username
            and not effective_bangumi_token
        ):
            return JSONResponse(
                {
                    "error": "no_profile_signal_sources",
                    "detail": (
                        "只选择 Bangumi 初始化时，需提供个人令牌（推荐，自动识别当前用户）、"
                        "公开用户名，或先在浏览器登录 bgm.tv 让扩展自动识别。"
                    ),
                },
                status_code=409,
            )
        if (
            "bangumi" in effective_sources
            and not effective_bangumi_username
            and not effective_bangumi_token
        ):
            warnings.append("Bangumi 未填写令牌或公开用户名：本次仅启用条目发现，不提供画像信号。")

        # Reserve the init run before source opt-in can hot-reload runtime
        # components. The durable init-active gate is therefore already
        # visible to an old controller, and the setup reload keeps the new
        # controller fully suspended until the guided-init wrapper finishes.
        run_id = uuid.uuid4().hex
        if not coord.try_start(run_id):
            return JSONResponse({"error": "already_running"}, status_code=409)

        setup_runtime_suspended = False

        async def _abort_reserved_start(reason: str) -> None:
            coord.reset_to_idle(run_id, reason=reason)
            if setup_runtime_suspended and not bool(getattr(ctx, "degraded", False)):
                with suppress(Exception):
                    await _restart_background_tasks_after_event_recovery()

        if selected_sources is not None:
            # With a token the username was resolved from /v0/me; persist that so
            # status/config reflect the real account. Otherwise keep the explicit
            # supplied value (None = leave the configured username untouched).
            username_to_persist = (
                effective_bangumi_username if effective_bangumi_token else selected_bangumi_username
            )
            setup_runtime_suspended = await _persist_guided_init_source_opt_in(
                sources_to_persist,
                bangumi_username=username_to_persist,
                bangumi_token=selected_bangumi_token,
                github_username=(
                    effective_github_username if "github" in effective_sources else None
                ),
                github_token=selected_github_token,
                v2ex_username=(effective_v2ex_username if "v2ex" in effective_sources else None),
            )

        # Critical-section revalidation: prereqs may have lapsed between the
        # status poll and now. On a miss, roll the reservation back to idle so
        # no stuck row remains (review R2 A-2). B站 login is only a prerequisite
        # when bilibili is among the selected sources.
        if "bilibili" in effective_sources:
            bili = await ctx.init_prereqs.bilibili_check()
            if bili != "ok":
                await _abort_reserved_start("bilibili_not_logged_in")
                return JSONResponse(
                    {
                        "error": "bilibili_not_logged_in",
                        "detail": ctx.init_prereqs.peek_bilibili_detail(),
                    },
                    status_code=409,
                )
        chat = await ctx.init_prereqs.chat_ready()
        if not chat:
            await _abort_reserved_start("llm_not_ready")
            # Propagate the classified cause the live probe just diagnosed
            # (无效 API Key / 服务不可达 / 模型不存在) so the rejection is
            # actionable rather than a generic "not ready" (project rule 7).
            chat_detail = ctx.init_prereqs.peek_chat_detail() or "AI 服务还没配好或当前不可用。"
            return JSONResponse({"error": "llm_not_ready", "detail": chat_detail}, status_code=409)
        if _embedding_required_for_init() and not await _health_embedding_ready(strict=True):
            pulling = await _maybe_autostart_embedding_pull()
            await _abort_reserved_start("embedding_not_ready")
            detail = (
                _repair_progress_detail()
                if pulling
                else "向量模型还没就绪。请在初始化页点「修复向量模型」，或等待自动下载完成后重试。"
            )
            return JSONResponse({"error": "embedding_not_ready", "detail": detail}, status_code=409)
        with suppress(Exception):
            await _maybe_autostart_embedding_pull()

        registry = getattr(ctx, "task_registry", None)
        # Capability admission may remove a discovery-only source from this
        # one personal-profile run while keeping its explicit opt-in persisted.
        # Pass that filtered set to the wrapper; recomputing from the original
        # checkbox payload would silently add the skipped source back.
        pipeline_sources = effective_sources if selected_sources is not None else None
        if registry is not None:
            task = registry.track(
                "guided_init",
                _run_guided_init_wrapper(
                    run_id,
                    selected_sources=pipeline_sources,
                    bangumi_username=effective_bangumi_username,
                    bangumi_token=effective_bangumi_token,
                    github_username=effective_github_username,
                    github_token=effective_github_token,
                    v2ex_username=effective_v2ex_username,
                    force=force,
                    reset_cognition=reset_cognition,
                    llm_concurrency=llm_concurrency,
                ),
            )
        else:
            task = asyncio.create_task(
                _run_guided_init_wrapper(
                    run_id,
                    selected_sources=pipeline_sources,
                    bangumi_username=effective_bangumi_username,
                    bangumi_token=effective_bangumi_token,
                    github_username=effective_github_username,
                    github_token=effective_github_token,
                    v2ex_username=effective_v2ex_username,
                    force=force,
                    reset_cognition=reset_cognition,
                    llm_concurrency=llm_concurrency,
                )
            )
        coord.attach_task(run_id, task)
        return JSONResponse(
            {"run_id": run_id, **coord.get_status(), "warnings": warnings},
            status_code=202,
        )

    # ── embedding one-click repair (v0.3.155+) ──────────────────────────
    # Single-flight background `ollama pull` so the popup's "语义去重未启用"
    # banner can actually FIX a missing/corrupt bge-m3 instead of retrying a
    # call that can never succeed. State is process-local (mirrors the
    # readiness cache): a restart simply forgets an old run.
    _embedding_repair_state: dict[str, Any] = {
        "running": False,
        "model": "",
        "status": "",
        "completed": 0,
        "total": 0,
        "done": False,
        "ok": False,
        "error": "",
    }
    _embedding_repair_lock = asyncio.Lock()
    _max_embedding_repair_actions = 3

    def _repair_progress_detail() -> str:
        """Human progress line for the in-flight pull (init pages render it)."""
        state = _embedding_repair_state
        model = str(state.get("model") or "向量模型")
        completed = int(state.get("completed") or 0)
        total = int(state.get("total") or 0)
        if total > 0:
            pct = min(99, round(completed * 100 / total))
            done_mb = completed // (1024 * 1024)
            total_mb = total // (1024 * 1024)
            return f"正在下载 {model}：{pct}%（{done_mb}MB / {total_mb}MB），完成后自动就绪。"
        return f"正在下载 {model}（准备中…），完成后自动就绪。"

    def _progress_int(value: object) -> int:
        if isinstance(value, int):
            return value
        if value is None:
            return 0
        try:
            return int(str(value))
        except ValueError:
            return 0

    def _embedding_pull_progress_view() -> dict[str, object]:
        """Combined app-local repair + process-global auto-pull progress."""
        snap = embedding_progress.snapshot()
        if bool(snap.get("running")):
            return {
                "running": True,
                "completed": _progress_int(snap.get("completed")),
                "total": _progress_int(snap.get("total")),
                "status_text": str(snap.get("status_text") or _repair_progress_detail()),
            }
        if _embedding_repair_state["running"]:
            return {
                "running": True,
                "completed": _progress_int(_embedding_repair_state.get("completed")),
                "total": _progress_int(_embedding_repair_state.get("total")),
                "status_text": _repair_progress_detail(),
            }
        return {
            "running": False,
            "completed": 0,
            "total": 0,
            "status_text": str(snap.get("status_text") or ""),
        }

    def _provider_error_detail(detail: str) -> str:
        base = detail.strip() or "Ollama 响应异常。"
        return (
            f"{base} 请升级 Ollama 到较新版本，确认 11434 端口没有被其他程序占用；"
            "如果本机回环被代理劫持，请关闭代理或设置 NO_PROXY=127.0.0.1,localhost 后重试。"
        )

    async def _run_embedding_repair(base_url: str, model: str) -> None:
        from openbiliclaw.llm.ollama_diagnostics import pull_ollama_model

        def _on_progress(status: str, completed: int, total: int) -> None:
            embedding_progress.report_pull(status, completed, total)
            _embedding_repair_state.update(
                {"status": status, "completed": completed, "total": total}
            )

        try:
            ok, error = await pull_ollama_model(base_url, model, on_progress=_on_progress)
        except Exception as exc:  # defensive: pull_ollama_model shouldn't raise
            ok, error = False, f"{type(exc).__name__}: {exc}"
        embedding_progress.mark_pull_done(ok, error)
        _embedding_repair_state.update({"running": False, "done": True, "ok": ok, "error": error})
        if ok:
            logger.info("Embedding repair: pulled %s successfully", model)
            # Next health/init-status poll re-probes immediately, so the
            # banner and checklist green up without waiting out the TTL.
            _expire_embedding_ready_cache()
        else:
            logger.warning("Embedding repair: pull %s failed: %s", model, error)

    async def _maybe_autostart_embedding_pull() -> bool:
        """Best-effort auto-pull for a locally hosted missing/broken model.

        Also self-heals ``DIAG_NOT_RUNNING`` (managed Ollama simply not up yet)
        by starting it first — reusing the same guards the manual repair
        endpoint applies — then re-diagnosing so a genuinely missing/broken
        model still gets auto-pulled. Any guard miss or start failure just
        leaves the manual "修复向量模型" path to fix it (stays non-blocking).
        """
        emb = getattr(getattr(getattr(ctx, "config", None), "llm", None), "embedding", None)
        if str(getattr(emb, "provider", "") or "").strip().lower() != "ollama":
            return False
        try:
            base_url, model = _embedding_ollama_target()
            from openbiliclaw.llm.ollama_diagnostics import (
                DIAG_MODEL_BROKEN,
                DIAG_MODEL_MISSING,
                DIAG_NOT_RUNNING,
                diagnose_ollama_embedding,
                ollama_embedding_disk_space_error,
            )
            from openbiliclaw.runtime.ollama_supervisor import (
                effective_ollama_endpoint,
                ensure_managed_ollama,
                is_loopback,
                may_manage_ollama_endpoint,
                ollama_required,
            )

            if not is_loopback(base_url):
                return False
            async with _embedding_repair_lock:
                if _embedding_repair_state["running"] or bool(
                    embedding_progress.snapshot().get("running")
                ):
                    return True
                code, _detail = await diagnose_ollama_embedding(base_url, model)
                if code == DIAG_NOT_RUNNING:
                    cfg = ctx.config
                    endpoint = effective_ollama_endpoint(cfg)
                    may_manage = (
                        bool(getattr(getattr(cfg, "autostart", None), "manage_ollama", False))
                        and ollama_required(cfg)
                        and is_loopback(endpoint)
                        and may_manage_ollama_endpoint(endpoint)
                    )
                    if not may_manage or not ensure_managed_ollama(endpoint):
                        return False
                    # Re-diagnose now that the daemon is up: a missing/broken
                    # model falls through to the auto-pull below.
                    code, _detail = await diagnose_ollama_embedding(base_url, model)
                if code not in {DIAG_MODEL_MISSING, DIAG_MODEL_BROKEN}:
                    return False
                if ollama_embedding_disk_space_error(model):
                    return False
                _embedding_repair_state.update(
                    {
                        "running": True,
                        "model": model,
                        "status": "starting",
                        "completed": 0,
                        "total": 0,
                        "done": False,
                        "ok": False,
                        "error": "",
                    }
                )
                embedding_progress.mark_pull_running(model)
                coro = _run_embedding_repair(base_url, model)
                try:
                    registry = getattr(ctx, "task_registry", None)
                    if registry is not None:
                        registry.track("embedding_repair", coro)
                    else:
                        task = asyncio.create_task(coro)
                        _fire_and_forget_tasks.add(task)
                        task.add_done_callback(_fire_and_forget_tasks.discard)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    _embedding_repair_state.update(
                        {"running": False, "done": True, "ok": False, "error": error}
                    )
                    embedding_progress.mark_pull_done(False, error)
                    coro.close()
                    logger.debug("auto embedding pull scheduling failed", exc_info=True)
                    return False
            logger.info("Auto-started embedding pull for %s at init", model)
            return True
        except Exception:
            logger.debug("auto embedding pull skipped", exc_info=True)
            return False

    @app.post("/api/embedding/repair")
    async def start_embedding_repair(request: Request) -> JSONResponse:
        """(Re-)pull the configured Ollama embedding model (local-only).

        409s: not Ollama-backed (fix config instead), unmanaged Ollama is not
        running, managed Ollama failed to start, or a repair already in flight.
        """
        if not _get_auth_gate().is_trusted_local(request):
            return JSONResponse({"error": "local_only"}, status_code=403)
        emb = getattr(getattr(getattr(ctx, "config", None), "llm", None), "embedding", None)
        provider = str(getattr(emb, "provider", "") or "").strip().lower()
        if provider != "ollama":
            return JSONResponse(
                {
                    "error": "unsupported_provider",
                    "detail": "一键修复只支持本地 Ollama embedding；"
                    "请到设置页把 Embedding Provider 设为 ollama 后重试。",
                },
                status_code=409,
            )
        base_url, model = _embedding_ollama_target()

        from openbiliclaw.llm.ollama_diagnostics import (
            DIAG_DISK_FULL,
            DIAG_ERROR,
            DIAG_MODEL_BROKEN,
            DIAG_MODEL_MISSING,
            DIAG_MODEL_OOM,
            DIAG_MODEL_PATH_ENCODING,
            DIAG_NETWORK,
            DIAG_NOT_RUNNING,
            DIAG_OK,
            diagnose_ollama_embedding,
            ollama_embedding_disk_space_error,
        )

        async with _embedding_repair_lock:
            if _embedding_repair_state["running"]:
                return JSONResponse({"error": "already_running"}, status_code=409)
            actions = 0
            provider_error_restarted = False
            while True:
                code, detail = await diagnose_ollama_embedding(base_url, model)
                if code == DIAG_OK:
                    _expire_embedding_ready_cache()
                    return JSONResponse({"ok": True, "already_ok": True, "model": model})
                if code == DIAG_NOT_RUNNING:
                    from openbiliclaw.runtime.ollama_supervisor import (
                        effective_ollama_endpoint,
                        ensure_managed_ollama,
                        is_loopback,
                        may_manage_ollama_endpoint,
                        ollama_required,
                    )

                    cfg = ctx.config
                    endpoint = effective_ollama_endpoint(cfg)
                    may_manage = (
                        bool(getattr(getattr(cfg, "autostart", None), "manage_ollama", False))
                        and ollama_required(cfg)
                        and is_loopback(endpoint)
                        and may_manage_ollama_endpoint(endpoint)
                    )
                    if not may_manage:
                        return JSONResponse(
                            {"error": "not_running", "detail": detail},
                            status_code=409,
                        )
                    if actions >= _max_embedding_repair_actions:
                        return JSONResponse(
                            {
                                "error": "not_running",
                                "detail": f"自动修复已达到上限：{detail}",
                            },
                            status_code=409,
                        )
                    if not ensure_managed_ollama(endpoint):
                        return JSONResponse(
                            {"error": "not_running", "detail": detail},
                            status_code=409,
                        )
                    actions += 1
                    continue
                if code == DIAG_MODEL_PATH_ENCODING:
                    from openbiliclaw.runtime.ollama_supervisor import (
                        ollama_models_relocation_candidate,
                        restart_managed_ollama_with_models_dir,
                    )

                    manual_detail = (
                        "检测到模型路径含非 ASCII 字符（常见于中文 Windows 用户名），"
                        "llama-server 无法从该路径加载模型，重新下载不能解决。"
                        "请手动设置系统环境变量 OLLAMA_MODELS 为纯英文路径"
                        f"（如 D:\\ollama\\models），然后重启 Ollama 并重新拉取 {model}。"
                    )
                    models_dir = ollama_models_relocation_candidate()
                    if models_dir is None:
                        return JSONResponse(
                            {"error": "manual_fix_required", "detail": manual_detail},
                            status_code=409,
                        )
                    restarted, reason = restart_managed_ollama_with_models_dir(models_dir)
                    if not restarted and reason == "external_ollama":
                        return JSONResponse(
                            {
                                "error": "external_ollama",
                                "detail": (
                                    "检测到外部启动的 Ollama，我们无法带新模型目录重启它；"
                                    "请手动设置 OLLAMA_MODELS，或退出外部 Ollama 后重试。"
                                ),
                            },
                            status_code=409,
                        )
                    if not restarted:
                        return JSONResponse(
                            {
                                "error": "restart_failed",
                                "detail": (
                                    "迁移模型目录后重启 Ollama 失败，"
                                    "请重启应用或手动设置 OLLAMA_MODELS。"
                                ),
                            },
                            status_code=409,
                        )
                    if disk_error := ollama_embedding_disk_space_error(model):
                        disk_code, disk_detail = disk_error
                        return JSONResponse(
                            {"error": disk_code, "detail": disk_detail},
                            status_code=409,
                        )
                    break
                if code in {DIAG_DISK_FULL, DIAG_NETWORK, DIAG_MODEL_OOM}:
                    return JSONResponse({"error": code, "detail": detail}, status_code=409)
                if code == DIAG_ERROR:
                    if not provider_error_restarted:
                        from openbiliclaw.runtime.ollama_supervisor import (
                            effective_ollama_endpoint,
                            is_loopback,
                            may_manage_ollama_endpoint,
                            ollama_required,
                            restart_managed_ollama,
                        )

                        cfg = ctx.config
                        endpoint = effective_ollama_endpoint(cfg)
                        may_manage = (
                            bool(getattr(getattr(cfg, "autostart", None), "manage_ollama", False))
                            and ollama_required(cfg)
                            and is_loopback(endpoint)
                            and may_manage_ollama_endpoint(endpoint)
                        )
                        provider_error_restarted = True
                        if may_manage and actions < _max_embedding_repair_actions:
                            restarted, _reason = restart_managed_ollama()
                            if restarted:
                                actions += 1
                                continue
                    return JSONResponse(
                        {"error": "provider_error", "detail": _provider_error_detail(detail)},
                        status_code=409,
                    )
                if code in {DIAG_MODEL_MISSING, DIAG_MODEL_BROKEN}:
                    if disk_error := ollama_embedding_disk_space_error(model):
                        disk_code, disk_detail = disk_error
                        return JSONResponse(
                            {"error": disk_code, "detail": disk_detail},
                            status_code=409,
                        )
                    break
                return JSONResponse(
                    {"error": "provider_error", "detail": _provider_error_detail(detail)},
                    status_code=409,
                )
            # model_missing / model_broken (and path migration) are pull-fixable.
            _embedding_repair_state.update(
                {
                    "running": True,
                    "model": model,
                    "status": "starting",
                    "completed": 0,
                    "total": 0,
                    "done": False,
                    "ok": False,
                    "error": "",
                }
            )
            embedding_progress.mark_pull_running(model)
            registry = getattr(ctx, "task_registry", None)
            coro = _run_embedding_repair(base_url, model)
            if registry is not None:
                registry.track("embedding_repair", coro)
            else:
                task = asyncio.create_task(coro)
                _fire_and_forget_tasks.add(task)
                task.add_done_callback(_fire_and_forget_tasks.discard)
        return JSONResponse(
            {"started": True, "model": model, "diagnosis": code, "detail": detail},
            status_code=202,
        )

    @app.get("/api/embedding/repair")
    async def embedding_repair_status() -> JSONResponse:
        """Progress of the in-flight (or last finished) embedding repair."""
        return JSONResponse(dict(_embedding_repair_state))

    @app.post("/api/init/cancel")
    async def cancel_guided_init(request: Request) -> JSONResponse:
        """Cooperatively cancel the in-flight guided init (local-only)."""
        if not _get_auth_gate().is_trusted_local(request):
            return JSONResponse({"error": "local_only"}, status_code=403)
        coord = ctx.init_coordinator
        await coord.reconcile_orphaned_run()
        run = ctx.database.get_latest_init_run() if ctx.database is not None else None
        if run is None or not coord.init_active():
            return JSONResponse({"error": "not_running"}, status_code=409)
        cancelled = await coord.cancel_current_run(run["run_id"])
        if not cancelled:
            return JSONResponse({"error": "not_running"}, status_code=409)
        return JSONResponse({"cancelling": True, "run_id": run["run_id"]}, status_code=202)

    @app.get("/api/image-proxy", response_model=None)
    async def image_proxy(
        url: str = Query(..., description="URL-encoded image URL to proxy"),
    ) -> Response:
        """Proxy whitelisted remote cover images through the local backend.

        Cache-first: a cached copy IS the image for that URL (the URL identifies
        it), so serve it immediately instead of paying a ~2s upstream round-trip
        on every load. The old code re-fetched on the success path and only read
        the cache when the upstream failed, so covers stayed slow even when
        cached. On a miss, fetch via ``image_cache.fetch_cover_bytes`` (whitelist
        / redirect / size validation), cache it, and serve. ``X-Image-Cache``
        reports hit/miss; slow misses are logged for diagnosis.
        """
        started = time.monotonic()
        try:
            result = await image_fetch_coordinator.fetch(url)
        except CoverFetchError as exc:
            host, cache_id = image_log_identity(url)
            logger.debug(
                "image-proxy FAIL status=%d host=%s cache=%s",
                exc.status_code,
                host,
                cache_id,
            )
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not result.cache_hit and elapsed_ms > 800:
            host, cache_id = image_log_identity(url)
            logger.debug(
                "image-proxy MISS elapsed_ms=%d host=%s cache=%s",
                elapsed_ms,
                host,
                cache_id,
            )
        return Response(
            content=result.data,
            media_type=result.content_type,
            headers={
                "Cache-Control": "public, max-age=86400",
                "X-Content-Type-Options": "nosniff",
                "X-Image-Cache": "hit" if result.cache_hit else "miss",
            },
        )

    @app.post("/api/bilibili/cookie", response_model=BilibiliCookieResponse, deprecated=True)
    async def sync_bilibili_cookie(
        payload: BilibiliCookieIn,
    ) -> BilibiliCookieResponse | JSONResponse:
        """Receive a Bilibili cookie from the browser extension and persist
        it server-side so the backend can call B 站 API as the user.

        **Deprecated** in favour of ``POST /api/sources/bilibili/credential``,
        which speaks the one write shape every platform now shares. This route
        stays, forwards into that same flow, and keeps its response byte-shape
        exactly — installed extensions parse it, and breaking them would take a
        user's login away with no visible cause. The deprecation marker is not
        decorative: ``scripts/source_contract_metrics.py`` counts undeprecated
        credential-write shapes, so leaving it off would keep the metric
        reporting five ways to store a credential when there is now one.

        Replaces the manual "F12 → Network → copy cookie → paste into
        wizard" flow. The extension already runs on bilibili.com and has
        the ``cookies`` Chrome permission, so it's the natural place to
        get a fresh, valid cookie. We auto-sync on first install and
        whenever ``chrome.cookies.onChanged`` fires.

        Persistence: writes to ``data/bilibili_cookie.json`` (the runtime
        cookie source) AND ``config.toml [bilibili].cookie`` (kept in sync
        as a mirror for ``config-show``). Then rebuilds the runtime
        BilibiliAPIClient via the same ``rebuild_from_config`` path that
        the config-update endpoint uses, so any in-flight handlers see
        the new cookie on their next call.

        Security: the backend is bound to 127.0.0.1 by default, so this
        endpoint is only reachable from the user's own machine. CORS
        already accepts ``*`` (set when the app is built); no auth token
        is needed for an API that lives behind a localhost-only listener.
        Users who flip ``--host 0.0.0.0`` should put their own auth
        layer in front of the backend.
        """
        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import load_config_with_diagnostics

        cookie_value = payload.cookie.strip()
        if not cookie_value:
            return BilibiliCookieResponse(
                ok=False,
                authenticated=False,
                message="cookie payload is empty",
                error_code="empty_cookie",
            )

        # gui-init D1.2: while guided init runs, the extension keeps auto-syncing
        # the cookie. Don't validate (~30s round-trip) or rebuild the runtime
        # (which would swap the BilibiliAPIClient mid-init). Same effective
        # cookie → silent 200 no-op so the extension's auto-sync doesn't error;
        # a genuinely different cookie → 409 so the user learns the switch
        # didn't take (it applies after init), rather than being dropped.
        if _init_active_now():
            effective_cookie = ""
            try:
                _cfg, _ = load_config_with_diagnostics()
                _cfg = _pin_active_runtime_config(_cfg)
                effective_cookie = (_cfg.bilibili.cookie or "").strip()
                if not effective_cookie:
                    effective_cookie = AuthManager(data_dir=_cfg.data_path).load_cookie().strip()
            except Exception:
                effective_cookie = ""
            if cookie_value == effective_cookie and effective_cookie:
                return BilibiliCookieResponse(
                    ok=True,
                    authenticated=True,
                    message="Cookie 未变，初始化进行中无需重新同步。",
                )
            return JSONResponse(
                {
                    "error": "init_running",
                    "detail": "初始化进行中，暂不能切换 Cookie，请稍后再试。",
                },
                status_code=409,
            )

        # ``payload.validate_with_bilibili`` is deliberately not consulted. It is
        # still accepted on the wire (installed extensions send it), but a
        # request cannot buy a weaker check: sending false used to persist a
        # dead cookie with the probe never called, which is the exact hole that
        # got ``validate_live`` deleted from the unified endpoint. Leaving the
        # identical switch running on the deprecated route made that deletion
        # cosmetic — "the extension always sends true" is a statement about the
        # extension, not about who can reach this port.
        result = await _write_source_credential(
            "bilibili",
            kind="cookie",
            value=cookie_value,
            source=payload.source,
        )
        if not result.accepted:
            return BilibiliCookieResponse(
                ok=False,
                authenticated=False,
                message=result.message or "Cookie validation failed; not saved.",
                error_code=result.error_code,
            )

        return BilibiliCookieResponse(
            ok=True,
            authenticated=result.authenticated,
            username=result.username,
            user_id=result.user_id,
            message=(
                "Cookie synced and runtime refreshed."
                if result.runtime_refreshed
                else "Cookie already synced; runtime unchanged."
            ),
        )

    def _qr_cookie_header(url: str) -> str:
        """Extract a browser-style Cookie header from Bilibili's QR success URL."""
        if not url:
            return ""
        qs = dict(parse_qsl(urlsplit(url).query))
        session = qs.get("session", "")
        if session:
            return session
        pairs = {
            key: qs[key]
            for key in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid")
            if key in qs
        }
        if pairs:
            return "; ".join(f"{key}={value}" for key, value in pairs.items())
        return ""

    @app.post("/api/bilibili/auth/qrcode")
    async def bilibili_qrcode_create() -> dict[str, Any]:
        """Create a Bilibili web QR login session for the mobile app."""
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.config import load_config

        cfg = _pin_active_runtime_config(load_config())
        client = BilibiliAPIClient(
            cookie="",
            proxy=(getattr(cfg.bilibili, "proxy", None) or None),
        )
        try:
            data = await client.generate_qrcode()
            return {
                "ok": True,
                "qrcode_key": str(data.get("qrcode_key", "") or ""),
                "qrcode_url": str(data.get("url", "") or ""),
                "expires_in": 180,
                "expires_at": "",
            }
        finally:
            await client.close()

    @app.get("/api/bilibili/auth/qrcode/poll")
    async def bilibili_qrcode_poll(qrcode_key: str = Query(...)) -> dict[str, Any]:
        """Poll a Bilibili web QR login session."""
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.config import load_config

        cfg = _pin_active_runtime_config(load_config())
        client = BilibiliAPIClient(
            cookie="",
            proxy=(getattr(cfg.bilibili, "proxy", None) or None),
        )
        try:
            data = await client.poll_qrcode(qrcode_key)
        finally:
            await client.close()

        status = str(data.get("status", "pending"))
        user = None
        if status == "confirmed":
            cookie_header = _qr_cookie_header(str(data.get("url", "") or ""))
            if cookie_header:
                result = await _write_source_credential(
                    "bilibili",
                    kind="cookie",
                    value=cookie_header,
                    source="mobile_qrcode",
                )
                if result.accepted:
                    status = "logged_in"
                    user = {
                        "mid": result.user_id or 0,
                        "name": result.username or "",
                        "face": "",
                        "vip": False,
                    }
        return {
            "ok": True,
            "status": status,
            "user": user,
            "message": str(data.get("message", "") or ""),
        }

    @app.get("/api/bilibili/auth/status")
    async def bilibili_auth_status() -> dict[str, Any]:
        """Return Bilibili login state for the mobile native player."""
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config

        cfg = _pin_active_runtime_config(load_config())
        cookie = resolve_runtime_cookie(
            data_dir=cfg.data_path,
            configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
        )
        if not cookie:
            return {
                "ok": True,
                "platform": "bilibili",
                "status": "anonymous",
                "user": None,
                "scopes": ["video", "danmaku", "comment"],
                "expires_at": "",
            }

        client = BilibiliAPIClient(
            cookie=cookie,
            proxy=(getattr(cfg.bilibili, "proxy", None) or None),
        )
        try:
            nav = await client.get_nav_info()
        except Exception:
            return {
                "ok": True,
                "platform": "bilibili",
                "status": "expired",
                "user": None,
                "scopes": ["video", "danmaku", "comment"],
                "expires_at": "",
            }
        finally:
            await client.close()

        if not nav.is_login:
            return {
                "ok": True,
                "platform": "bilibili",
                "status": "expired",
                "user": None,
                "scopes": ["video", "danmaku", "comment"],
                "expires_at": "",
            }
        return {
            "ok": True,
            "platform": "bilibili",
            "status": "logged_in",
            "user": {
                "mid": nav.mid,
                "name": nav.uname,
                "face": "",
                "vip": False,
            },
            "scopes": ["video", "danmaku", "comment", "fav", "later"],
            "expires_at": "",
        }

    @app.post("/api/bilibili/player/play-url")
    async def bilibili_play_url(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        """Resolve Bilibili playback URLs for the mobile native player."""
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config

        bvid = str(payload.get("bvid", "") or "").strip()
        if not bvid:
            raise HTTPException(status_code=400, detail="missing bvid")
        cid = payload.get("cid")
        qn = payload.get("qn", 80)
        preferred_codec = str(payload.get("preferred_codec", "avc") or "avc")

        cfg = _pin_active_runtime_config(load_config())
        cookie = resolve_runtime_cookie(
            data_dir=cfg.data_path,
            configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
        )
        if not cookie:
            raise HTTPException(status_code=401, detail="B站 Cookie 未配置或已失效")

        client = BilibiliAPIClient(
            cookie=cookie,
            proxy=(getattr(cfg.bilibili, "proxy", None) or None),
        )
        try:
            info = await client.get_play_info(
                bvid=bvid,
                cid=int(cid) if cid is not None else None,
                qn=int(qn) if qn else 80,
                preferred_codec=preferred_codec,
            )
            return {"ok": True, **info}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            await client.close()

    @app.get("/api/bilibili/video/relation")
    async def bilibili_video_relation(bvid: str = Query(...)) -> dict[str, Any]:
        """Get the logged-in user's Bilibili video interaction state."""
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config

        cfg = _pin_active_runtime_config(load_config())
        cookie = resolve_runtime_cookie(
            data_dir=cfg.data_path,
            configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
        )
        if not cookie:
            raise HTTPException(status_code=401, detail="B站 Cookie 未配置或已失效")
        client = BilibiliAPIClient(
            cookie=cookie,
            proxy=(getattr(cfg.bilibili, "proxy", None) or None),
        )
        try:
            return {"ok": True, **await client.get_video_relation_state(bvid)}
        finally:
            await client.close()

    @app.post("/api/bilibili/video/like")
    async def bilibili_video_like(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config

        bvid = str(payload.get("bvid", "") or "").strip()
        like = bool(payload.get("like", True))
        cfg = _pin_active_runtime_config(load_config())
        cookie = resolve_runtime_cookie(
            data_dir=cfg.data_path,
            configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
        )
        if not cookie:
            raise HTTPException(status_code=401, detail="B站 Cookie 未配置或已失效")
        client = BilibiliAPIClient(
            cookie=cookie,
            proxy=(getattr(cfg.bilibili, "proxy", None) or None),
        )
        try:
            await client.like_video(bvid, like=like)
            return {"ok": True}
        finally:
            await client.close()

    @app.post("/api/bilibili/video/coin")
    async def bilibili_video_coin(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config

        bvid = str(payload.get("bvid", "") or "").strip()
        multiply = int(payload.get("multiply", 1) or 1)
        select_like = bool(payload.get("select_like", False))
        cfg = _pin_active_runtime_config(load_config())
        cookie = resolve_runtime_cookie(
            data_dir=cfg.data_path,
            configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
        )
        if not cookie:
            raise HTTPException(status_code=401, detail="B站 Cookie 未配置或已失效")
        client = BilibiliAPIClient(
            cookie=cookie,
            proxy=(getattr(cfg.bilibili, "proxy", None) or None),
        )
        try:
            await client.coin_video(bvid, multiply=multiply, select_like=select_like)
            return {"ok": True}
        finally:
            await client.close()

    @app.post("/api/bilibili/video/triple")
    async def bilibili_video_triple(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config

        bvid = str(payload.get("bvid", "") or "").strip()
        cfg = _pin_active_runtime_config(load_config())
        cookie = resolve_runtime_cookie(
            data_dir=cfg.data_path,
            configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
        )
        if not cookie:
            raise HTTPException(status_code=401, detail="B站 Cookie 未配置或已失效")
        client = BilibiliAPIClient(
            cookie=cookie,
            proxy=(getattr(cfg.bilibili, "proxy", None) or None),
        )
        try:
            await client.triple_video(bvid)
            return {"ok": True, "state": await client.get_video_relation_state(bvid)}
        finally:
            await client.close()

    @app.post("/api/bilibili/video/favorite")
    async def bilibili_video_favorite(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config

        bvid = str(payload.get("bvid", "") or "").strip()
        favorite = bool(payload.get("favorite", True))
        media_id = payload.get("media_id")
        cfg = _pin_active_runtime_config(load_config())
        cookie = resolve_runtime_cookie(
            data_dir=cfg.data_path,
            configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
        )
        if not cookie:
            raise HTTPException(status_code=401, detail="B站 Cookie 未配置或已失效")
        client = BilibiliAPIClient(
            cookie=cookie,
            proxy=(getattr(cfg.bilibili, "proxy", None) or None),
        )
        try:
            await client.favorite_video(
                bvid,
                media_id=int(media_id) if media_id is not None else None,
                favorite=favorite,
            )
            return {"ok": True, "state": await client.get_video_relation_state(bvid)}
        finally:
            await client.close()

    @app.post("/api/bilibili/video/watch-later")
    async def bilibili_video_watch_later(
        payload: Annotated[dict[str, Any], Body()],
    ) -> dict[str, Any]:
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config

        bvid = str(payload.get("bvid", "") or "").strip()
        add = bool(payload.get("add", True))
        cfg = _pin_active_runtime_config(load_config())
        cookie = resolve_runtime_cookie(
            data_dir=cfg.data_path,
            configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
        )
        if not cookie:
            raise HTTPException(status_code=401, detail="B站 Cookie 未配置或已失效")
        client = BilibiliAPIClient(
            cookie=cookie,
            proxy=(getattr(cfg.bilibili, "proxy", None) or None),
        )
        try:
            await client.watch_later_video(bvid, add=add)
            return {"ok": True, "state": await client.get_video_relation_state(bvid)}
        finally:
            await client.close()

    @app.get("/api/bilibili/video/related")
    async def bilibili_video_related(bvid: str = Query(...)) -> dict[str, Any]:
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config

        cfg = _pin_active_runtime_config(load_config())
        cookie = resolve_runtime_cookie(
            data_dir=cfg.data_path,
            configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
        )
        client = BilibiliAPIClient(
            cookie=cookie,
            proxy=(getattr(cfg.bilibili, "proxy", None) or None),
        )
        try:
            return {"ok": True, "items": await client.get_related_videos(bvid)}
        finally:
            await client.close()

    @app.get("/api/bilibili/video/comments")
    async def bilibili_video_comments(
        bvid: str = Query(...),
        limit: int = Query(20, ge=1, le=50),
    ) -> dict[str, Any]:
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config

        cfg = _pin_active_runtime_config(load_config())
        cookie = resolve_runtime_cookie(
            data_dir=cfg.data_path,
            configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
        )
        client = BilibiliAPIClient(
            cookie=cookie,
            proxy=(getattr(cfg.bilibili, "proxy", None) or None),
        )
        try:
            comments = await client.get_video_comments(bvid, limit=limit)
            return {
                "ok": True,
                "items": [
                    {
                        "mid": item.mid,
                        "uname": item.uname,
                        "message": item.message,
                        "like_count": item.like_count,
                    }
                    for item in comments
                ],
            }
        finally:
            await client.close()

    @app.post("/api/bilibili/auth/import")
    async def bilibili_auth_import(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        """Import a Bilibili cookie from the mobile WebView login."""
        cookies = payload.get("cookies") or {}
        if not isinstance(cookies, dict):
            raise HTTPException(status_code=400, detail="cookies must be an object")
        cookie_header = "; ".join(
            f"{str(key).strip()}={str(value).strip()}" for key, value in cookies.items()
        )
        result = await _write_source_credential(
            "bilibili",
            kind="cookie",
            value=cookie_header,
            source=str(payload.get("source", "mobile_webview")),
        )
        if not result.accepted:
            raise HTTPException(
                status_code=400,
                detail=result.message or "Cookie 校验失败",
            )
        return {
            "ok": True,
            "status": "logged_in" if result.authenticated else "anonymous",
            "user": {
                "mid": result.user_id or 0,
                "name": result.username or "",
                "face": "",
                "vip": False,
            },
        }

    @app.delete("/api/bilibili/auth/session")
    async def bilibili_auth_clear() -> dict[str, Any]:
        """Clear the backend's stored Bilibili cookie/session."""
        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import load_config

        cfg = _pin_active_runtime_config(load_config())
        AuthManager(cfg.data_path).clear_cookie()
        return {"ok": True}

    @app.post("/api/sources/dy/cookie", response_model=DouyinCookieResponse, deprecated=True)
    async def sync_douyin_cookie(payload: DouyinCookieIn) -> DouyinCookieResponse:
        """Receive a Douyin cookie from the browser extension.

        **Deprecated** in favour of ``POST /api/sources/douyin/credential``;
        forwards into it and keeps this response shape for installed
        extensions.

        This endpoint used to persist whatever header arrived, without any
        check, and the reason recorded here was that Douyin "has no stable nav
        endpoint that cleanly distinguishes 'logged out' from 'soft anti-bot
        returned HTTP 200 with empty data'".

        **That claim was false** (spec D11), and it was the sole reason Douyin
        reported "unverified" forever even with a perfectly valid cookie. A
        strip-down control experiment — same signer, same UA, same minute, only
        the 12 login cookies removed — showed
        ``/aweme/v1/web/user/profile/self/`` answers ``status_code=0`` plus a
        real ``user.uid`` when logged in and ``status_code=8`` / "用户未登录"
        when not: an explicit error code, not the ambiguity the claim feared.
        So a Douyin cookie is now probed *before* it lands, like B站's. The
        probe lives in :mod:`openbiliclaw.sources.douyin_login_probe`; do not
        reintroduce the old conclusion without repeating the experiment.
        """
        cookie_value = payload.cookie.strip()
        if not cookie_value:
            return DouyinCookieResponse(
                ok=False,
                has_cookie=False,
                message="cookie payload is empty",
                error_code="empty_cookie",
            )

        result = await _write_source_credential(
            "douyin", kind="cookie", value=cookie_value, source=payload.source
        )
        return DouyinCookieResponse(
            ok=result.accepted,
            has_cookie=result.accepted,
            cookie_names=list(result.cookie_names),
            message=result.message if not result.accepted else "Douyin Cookie synced.",
            error_code=result.error_code,
        )

    @app.post("/api/sources/x/cookie", response_model=XCookieResponse, deprecated=True)
    async def sync_x_cookie(payload: XCookieIn) -> XCookieResponse:
        """Receive an X (Twitter) cookie from the browser extension.

        **Deprecated** in favour of ``POST /api/sources/twitter/credential``;
        forwards into it and keeps this response shape.

        ``has_cookie`` is true only when BOTH ``auth_token`` and ``ct0`` are
        present — twitter-cli 401s without either. A jar missing them used to
        be stored anyway and reported as ``ok`` with ``has_cookie=false``; it is
        now refused, because storing a credential that provably cannot
        authenticate is the "silent write" spec D5 flagged. The browser
        extension already gated on both names before posting and keys its
        success branch off ``ok && has_cookie``, so it sees no change.
        """
        cookie_value = payload.cookie.strip()
        if not cookie_value:
            return XCookieResponse(
                ok=False,
                has_cookie=False,
                message="cookie payload is empty",
                error_code="empty_cookie",
            )

        result = await _write_source_credential(
            "twitter", kind="cookie", value=cookie_value, source=payload.source
        )
        return XCookieResponse(
            ok=result.accepted,
            has_cookie=result.accepted,
            cookie_names=list(result.cookie_names),
            message=result.message if not result.accepted else "X Cookie synced.",
            error_code=result.error_code,
        )

    @app.post("/api/sources/reddit/cookie", response_model=RedditCookieResponse, deprecated=True)
    async def sync_reddit_cookie(payload: RedditCookieIn) -> RedditCookieResponse:
        """Receive a Reddit cookie from the browser extension.

        **Deprecated** in favour of ``POST /api/sources/reddit/credential``;
        forwards into it and keeps this response shape.

        Reddit steady-state discovery defaults to rdt-cli. Instead of forcing
        users to run ``rdt login`` manually, the connected extension can read
        reddit.com cookies with Chrome's ``cookies`` permission and persist them
        in rdt-cli's own credential format.
        """
        from openbiliclaw.sources.reddit_tasks import _rdt_credential_file

        cookie_value = payload.cookie.strip()
        if not cookie_value:
            return RedditCookieResponse(
                ok=False,
                has_cookie=False,
                message="cookie payload is empty",
                error_code="empty_cookie",
            )

        result = await _write_source_credential(
            "reddit", kind="cookie", value=cookie_value, source=payload.source
        )
        credential_file = result.credential_file
        if not credential_file:
            # A refused cookie never reached the writer, so the path has to come
            # from the store itself — the settings page shows it either way, and
            # "where would this have been written" is the first thing a user
            # asks when a paste is rejected.
            with suppress(Exception):
                credential_file = str(_rdt_credential_file())

        return RedditCookieResponse(
            ok=result.accepted,
            has_cookie=result.accepted,
            cookie_names=list(result.cookie_names),
            credential_file=credential_file,
            message=(
                result.message
                if not result.accepted
                else "Reddit Cookie synced into rdt credential store."
            ),
            error_code=result.error_code,
        )

    @app.post("/api/init-completed")
    async def init_completed() -> dict[str, object]:
        """Notify the running server that ``openbiliclaw init`` has finished.

        Called by the CLI at the end of a successful init.  The handler
        broadcasts an ``init_completed`` event via WebSocket so the
        browser extension can immediately re-fetch profile, recommendations
        and activity data.  It also starts a replenishment refresh so the
        discovery pool is picked up without waiting for the next scheduler tick.
        """
        # Broadcast to extension
        with suppress(Exception):
            await ctx.event_hub.publish(
                {
                    "type": "init_completed",
                    "message": "初始化完成，画像与发现池已就绪。",
                }
            )
        await _request_runtime_replenishment(reason="init_completed", force=True)
        return {"ok": True}

    def _fail_closed_source_metadata(value: object) -> dict[str, object]:
        payload = value
        if isinstance(value, str):
            try:
                payload = json.loads(value)
            except (TypeError, ValueError):
                return {}
        return dict(payload) if isinstance(payload, dict) else {}

    def _serialize_recommendation_items(items: list[Any]) -> list[RecommendationOut]:
        def item_key_for(content: Any) -> str:
            explicit = str(getattr(content, "item_key", "") or "").strip()
            if explicit:
                return explicit
            bvid = str(getattr(content, "bvid", "") or "")
            return make_item_key(
                str(getattr(content, "source_platform", "") or "bilibili"),
                str(getattr(content, "content_id", "") or bvid),
                str(getattr(content, "content_url", "") or ""),
            )

        return [
            RecommendationOut(
                id=int(item.recommendation_id),
                bvid=str(item.content.bvid),
                item_key=item_key_for(item.content),
                title=str(item.content.title),
                up_name=str(item.content.up_name),
                cover_url=str(item.content.cover_url),
                expression=str(item.expression),
                topic_label=str(item.topic_label),
                presented=bool(item.presented),
                feedback_type=str(getattr(item, "feedback_type", "") or ""),
                content_id=str(getattr(item.content, "content_id", "") or item.content.bvid),
                content_url=str(getattr(item.content, "content_url", "") or ""),
                source_platform=str(getattr(item.content, "source_platform", "") or "bilibili"),
                published_at=str(getattr(item.content, "published_at", "") or ""),
                published_label=str(getattr(item.content, "published_label", "") or ""),
                content_type=str(getattr(item.content, "content_type", "") or "video"),
                body_text=str(getattr(item.content, "body_text", "") or ""),
                source_metadata=_fail_closed_source_metadata(
                    getattr(item.content, "source_metadata", {})
                ),
                duration=int(getattr(item.content, "duration", 0) or 0),
                view_count=int(getattr(item.content, "view_count", 0) or 0),
                like_count=int(getattr(item.content, "like_count", 0) or 0),
                danmaku_count=int(getattr(item.content, "danmaku_count", 0) or 0),
                favorite_count=int(
                    getattr(item.content, "favorite_count", 0)
                    or getattr(item.content, "collect_count", 0)
                    or 0
                ),
                comment_count=int(getattr(item.content, "comment_count", 0) or 0),
                share_count=int(getattr(item.content, "share_count", 0) or 0),
                rating_score=float(getattr(item.content, "rating_score", 0.0) or 0.0),
                rating_count=int(getattr(item.content, "rating_count", 0) or 0),
                source_rank=int(getattr(item.content, "source_rank", 0) or 0),
                up_mid=int(getattr(item.content, "up_mid", 0) or 0),
            )
            for item in items
        ]

    def _filter_recommendation_objects_for_latest_dislikes(items: list[Any]) -> list[Any]:
        """Recheck an in-flight serve batch at the final HTTP boundary."""

        if not items:
            return []
        from openbiliclaw.recommendation.exclusion import filter_recommendation_rows

        disliked_topics, _digest = _effective_recommendation_dislikes()
        if not disliked_topics:
            return items
        projected = [
            {
                "_item_index": index,
                "title": str(getattr(getattr(item, "content", None), "title", "")),
                "topic_label": str(getattr(item, "topic_label", "")),
                "topic_key": str(getattr(getattr(item, "content", None), "topic_key", "")),
                "topic_group": str(getattr(getattr(item, "content", None), "topic_group", "")),
                "pool_topic_label": str(
                    getattr(getattr(item, "content", None), "pool_topic_label", "")
                ),
                "description": str(getattr(getattr(item, "content", None), "description", "")),
                "body_text": str(getattr(getattr(item, "content", None), "body_text", "")),
                "up_name": str(getattr(getattr(item, "content", None), "up_name", "")),
                "tags": getattr(getattr(item, "content", None), "tags", []),
            }
            for index, item in enumerate(items)
        ]
        allowed = filter_recommendation_rows(projected, disliked_topics)
        return [items[cast("int", row["_item_index"])] for row in allowed]

    @app.websocket("/api/runtime-stream")
    async def runtime_stream(websocket: WebSocket) -> None:
        # The http auth middleware does NOT cover the websocket scope, so the
        # password gate must be enforced here before accepting the handshake.
        if not authorize_websocket(_get_auth_gate(), websocket):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        if bool(getattr(ctx, "degraded", False)):
            connected = False
            try:
                ctx.presence.on_connect()
                connected = True
                await websocket.send_json(
                    {
                        "type": "degraded",
                        "reason": str(getattr(ctx, "degraded_reason", "")),
                        "issues": _degraded_issues_payload(),
                    }
                )
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        raise WebSocketDisconnect
            except WebSocketDisconnect:
                pass
            finally:
                if connected:
                    ctx.presence.on_disconnect()
            return

        # Live revocation: an already-open socket from a remote client must stop
        # receiving events once its token is revoked (logout-all / password change
        # / rotate-secret). The http auth middleware never sees an established ws,
        # so re-check the revocation epoch here per-send and on a watchdog timer.
        _ws_gate = _get_auth_gate()
        _ws_is_local = _ws_gate.is_trusted_local(websocket)
        _ws_token = (
            None
            if (_ws_is_local or not _ws_gate.auth.enabled)
            else websocket_session_token(_ws_gate, websocket)
        )

        def _ws_revoked() -> bool:
            if not _ws_gate.auth.enabled or _ws_is_local:
                return False
            try:
                return not _ws_gate.token_valid(_ws_token)
            except Exception:
                return True  # DB unavailable → fail closed

        subscribe = getattr(ctx.event_hub, "subscribe", None)
        unsubscribe = getattr(ctx.event_hub, "unsubscribe", None)
        if not callable(subscribe) or not callable(unsubscribe):
            await websocket.close()
            return
        queue = await subscribe()
        connected = False

        async def _send_runtime_events() -> None:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=_RUNTIME_STREAM_HEARTBEAT_SECONDS,
                    )
                except TimeoutError:
                    event = {
                        "type": "runtime.heartbeat",
                        "sent_at": datetime.now(UTC).isoformat(),
                    }
                if _ws_revoked():
                    with suppress(Exception):
                        await websocket.close(code=4401)
                    return
                await websocket.send_json(event)

        async def _revocation_watchdog() -> None:
            # Close idle revoked sockets even when no events are flowing.
            while True:
                await asyncio.sleep(15)
                if _ws_revoked():
                    with suppress(Exception):
                        await websocket.close(code=4401)
                    return

        async def _receive_until_disconnect() -> None:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect

        try:
            ctx.presence.on_connect()
            connected = True
            client_name = str(websocket.query_params.get("client", "") or "").strip().lower()
            if client_name in {"background", "extension", "service-worker"}:
                from openbiliclaw.bilibili.auth import resolve_runtime_cookie
                from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie

                runtime_config = getattr(ctx, "config", None) or config
                # Browser-only sources persist just a boolean login heartbeat.
                # Ask once per runtime connection so settings immediately
                # reflects the current browser, without contacting a platform.
                await websocket.send_json(
                    {
                        "type": "xhs_login_state_sync_requested",
                        "reason": "runtime_connected",
                        "source": "runtime-stream",
                    }
                )
                await websocket.send_json(
                    {
                        "type": "zhihu_login_state_sync_requested",
                        "reason": "runtime_connected",
                        "source": "runtime-stream",
                    }
                )
                # X stores the complete browser Cookie server-side, so ask the
                # extension for the current jar on every fresh runtime
                # connection. This covers a backend restart/reconnect even
                # when the browser's cookies.onChanged event happened earlier.
                x_cfg = getattr(runtime_config.sources, "twitter", None)
                if x_cfg is not None and bool(getattr(x_cfg, "enabled", False)):
                    await websocket.send_json(
                        {
                            "type": "x_cookie_sync_requested",
                            "reason": "runtime_connected",
                            "source": "runtime-stream",
                        }
                    )
                with suppress(Exception):
                    cookie = resolve_runtime_cookie(
                        data_dir=runtime_config.data_path,
                        configured_cookie=runtime_config.bilibili.cookie,
                    )
                    if not str(cookie or "").strip():
                        await websocket.send_json(
                            {
                                "type": "bilibili_cookie_sync_requested",
                                "reason": "missing_cookie",
                                "source": "runtime-stream",
                            }
                        )
                with suppress(Exception):
                    dy_cfg = getattr(runtime_config.sources, "douyin", None)
                    if dy_cfg is not None and bool(getattr(dy_cfg, "enabled", False)):
                        dy_cookie = resolve_douyin_cookie(
                            data_dir=runtime_config.data_path,
                            cookie_env=str(
                                getattr(dy_cfg, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE")
                            ),
                        )
                        if not str(dy_cookie or "").strip():
                            await websocket.send_json(
                                {
                                    "type": "douyin_cookie_sync_requested",
                                    "reason": "missing_cookie",
                                    "source": "runtime-stream",
                                }
                            )
                with suppress(Exception):
                    from openbiliclaw.sources.reddit_tasks import _rdt_saved_credential_state

                    rd_cfg = getattr(runtime_config.sources, "reddit", None)
                    rd_backend = str(getattr(rd_cfg, "backend", "rdt") or "rdt").strip().lower()
                    if (
                        rd_cfg is not None
                        and bool(getattr(rd_cfg, "enabled", False))
                        and rd_backend == "rdt"
                    ):
                        state, _message = _rdt_saved_credential_state()
                        if state != "present":
                            await websocket.send_json(
                                {
                                    "type": "reddit_cookie_sync_requested",
                                    "reason": "missing_cookie",
                                    "source": "runtime-stream",
                                }
                            )

            writer = asyncio.create_task(_send_runtime_events())
            reader = asyncio.create_task(_receive_until_disconnect())
            watchdog = asyncio.create_task(_revocation_watchdog())
            done, pending = await asyncio.wait(
                {writer, reader, watchdog},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError):
                    await task
            for task in done:
                with suppress(WebSocketDisconnect):
                    task.result()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("runtime-stream closed after handler exception", exc_info=True)
        finally:
            if connected:
                ctx.presence.on_disconnect()
            await unsubscribe(queue)

    @app.on_event("startup")
    async def startup_refresh_loop() -> None:
        # Prune the cover-image cache on startup (consumed + unsaved content,
        # plus aged orphans). The periodic pass runs from RefreshRuntime.
        try:
            result = await asyncio.to_thread(
                cleanup_image_cache,
                database=getattr(ctx, "database", None),
                max_age_days=_IMAGE_CACHE_MAX_AGE_DAYS,
            )
            if result.removed:
                logger.info(
                    "Image cache cleanup: removed %d cover files (%.1f MB freed; "
                    "%d consumed, %d aged orphans, %d unrefetchable protected)",
                    result.removed,
                    result.freed_bytes / (1024 * 1024),
                    result.removed_consumed,
                    result.removed_aged_orphans,
                    result.protected_unrefetchable,
                )
        except Exception:
            logger.debug("Image cache cleanup failed", exc_info=True)

        # Guided-init crash recovery: fail any run left starting/running by a
        # prior crash so /api/init-status never reports a stuck running=true.
        # Must run even in degraded mode (before the early return below).
        try:
            reconciled = ctx.init_coordinator.reconcile_on_boot()
            if reconciled:
                logger.info("Reconciled %d stale guided-init run(s) on boot", reconciled)
        except Exception:
            logger.debug("Guided-init boot reconciliation failed", exc_info=True)

        try:
            released = await _reconcile_orphan_confusion_claims()
            if released:
                logger.info("Released %d orphan confusion claim(s) on boot", released)
        except Exception:
            logger.exception("Confusion claim boot reconciliation failed")

        # Recover every durable pending turn in insertion-order pages. The
        # scheduler is app-owned and therefore survives RuntimeContext swaps.
        await chat_reply_scheduler.start()

        if bool(getattr(ctx, "degraded", False)):
            return
        # Fence committed facts synchronously, then admit their potentially
        # provider-backed consume as an app-owned task.  A 401, a wedged model,
        # or a durable pending buffer must never hold the ASGI lifespan in
        # ``Waiting for application startup``.
        await _restart_background_tasks_after_event_recovery(
            recover_event_owner_synchronously=False,
        )

    @app.on_event("shutdown")
    async def shutdown_refresh_loop() -> None:
        apply_task = getattr(app.state, "config_apply_task", None)
        if apply_task is not None and not apply_task.done():
            apply_task.cancel()
            with suppress(asyncio.CancelledError):
                await apply_task
        reply_scheduler = getattr(app.state, "chat_reply_scheduler", None)
        if reply_scheduler is not None:
            with suppress(Exception):
                await reply_scheduler.close()
        feedback_scheduler = getattr(app.state, "feedback_batch_scheduler", None)
        if feedback_scheduler is not None:
            with suppress(Exception):
                await feedback_scheduler.close()
        app.state.event_recovery_task = None
        refresh_task = getattr(app.state, "refresh_task", None)
        if refresh_task is not None:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task
        account_sync_task = getattr(app.state, "account_sync_task", None)
        if account_sync_task is not None:
            account_sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await account_sync_task
        auto_update_task = getattr(app.state, "auto_update_task", None)
        if auto_update_task is not None:
            auto_update_task.cancel()
            with suppress(asyncio.CancelledError):
                await auto_update_task
        # Producers are stopped before their shared image lane. This prevents a
        # refresh tick from enqueueing new prefetch work during coordinator
        # shutdown; close then cancels both active and already-queued fetches.
        with suppress(Exception):
            await image_fetch_coordinator.close()
        # Drain the self-owned dialogue settlement queue; it is not covered by
        # the background-task cancellation above.
        settlement_queue = getattr(ctx, "dialogue_settlement_queue", None)
        if settlement_queue is not None:
            with suppress(Exception):
                await settlement_queue.shutdown(timeout=30)
        bangumi_client = getattr(ctx, "bangumi_client", None)
        close_bangumi = getattr(bangumi_client, "aclose", None)
        if callable(close_bangumi):
            with suppress(Exception):
                await close_bangumi()
        v2ex_client = getattr(ctx, "v2ex_client", None)
        close_v2ex = getattr(v2ex_client, "aclose", None)
        if callable(close_v2ex):
            with suppress(Exception):
                await close_v2ex()

    @app.get("/api/profile-summary", response_model=ProfileSummaryResponse)
    async def profile_summary(
        limit: int = Query(default=3, ge=1, le=20),
        cursor: str = "",
    ) -> ProfileSummaryResponse:
        try:
            profile = await ctx.soul_engine.get_profile()
        except Exception:
            return ProfileSummaryResponse(initialized=False)

        overrides_summary: dict[str, object] = {}
        _get_overrides = getattr(ctx.soul_engine, "get_overrides", None)
        if callable(_get_overrides):
            try:
                overrides_summary = _get_overrides().to_dict()
            except Exception:
                overrides_summary = {}

        # User-added entries per field, so the display caps below never hide a
        # manual edit (it would otherwise show in edit mode but not here).
        _list_edits = overrides_summary.get("list_edits", {})
        _interest_edits = overrides_summary.get("interest_edits", {})

        def _added_list(path: str) -> list[str]:
            edit = _list_edits.get(path) if isinstance(_list_edits, dict) else None
            add = edit.get("add", []) if isinstance(edit, dict) else []
            return [str(x) for x in add] if isinstance(add, list) else []

        def _added_domains(polarity: str) -> list[str]:
            edit = _interest_edits.get(polarity) if isinstance(_interest_edits, dict) else None
            domains = edit.get("add_domains", []) if isinstance(edit, dict) else []
            if not isinstance(domains, list):
                return []
            return [str(d.get("domain", "")) for d in domains if isinstance(d, dict)]

        from openbiliclaw.api.models import (
            AwarenessNoteOut,
            ContextModeOut,
            InsightHypothesisOut,
            InterestDomainOut,
            InterestSpecificOut,
            MBTIDimensionOut,
            MBTIOut,
            SpeculativeAvoidanceOut,
            SpeculativeInterestOut,
            SpeculativeSpecificOut,
            StylePreferenceOut,
        )
        from openbiliclaw.soul.avoidance_speculator import load_avoidance_state
        from openbiliclaw.soul.speculator import load_speculative_state

        prefs = profile.preferences

        # ── Core layer ──
        mbti_obj = getattr(getattr(profile, "core", None), "mbti", None)
        mbti_out = MBTIOut()
        mbti_type = str(getattr(mbti_obj, "type", "") or "") if mbti_obj is not None else ""
        if mbti_type:
            mbti_out = MBTIOut(
                type=mbti_type,
                dimensions={
                    k: MBTIDimensionOut(pole=str(v.pole), strength=float(v.strength))
                    for k, v in getattr(mbti_obj, "dimensions", {}).items()
                },
                confidence=float(getattr(mbti_obj, "confidence", 0.0)),
            )

        # ── Interest layer (tree structure) ──
        interest_layer = getattr(profile, "interest", None)

        def _domain_list(raw_domains: object) -> list[InterestDomainOut]:
            if not isinstance(raw_domains, list):
                return []
            return [
                InterestDomainOut(
                    domain=str(getattr(d, "domain", "")),
                    weight=float(getattr(d, "weight", 0.5)),
                    specifics=[
                        InterestSpecificOut(
                            name=str(getattr(s, "name", "")),
                            weight=float(getattr(s, "weight", 0.5)),
                        )
                        for s in getattr(d, "specifics", [])
                        if str(getattr(s, "name", "")).strip()
                    ],
                )
                for d in raw_domains
                if str(getattr(d, "domain", "")).strip()
            ]

        likes_out = _cap_keeping_user_added(
            _domain_list(getattr(interest_layer, "likes", [])),
            _added_domains("likes"),
            12,
            key=lambda d: d.domain,
        )
        dislikes_out = _cap_keeping_user_added(
            _domain_list(getattr(interest_layer, "dislikes", [])),
            _added_domains("dislikes"),
            8,
            key=lambda d: d.domain,
        )

        favorite_ups = _cap_keeping_user_added(
            [
                str(item).strip()
                for item in getattr(prefs, "favorite_up_users", [])
                if str(item).strip()
            ],
            _added_list("interest.favorite_up_users"),
            8,
        )

        # ── Surface layer ──
        style_raw = getattr(prefs, "style", None)
        style_out = StylePreferenceOut()
        if style_raw is not None:
            style_out = StylePreferenceOut(
                preferred_duration=str(getattr(style_raw, "preferred_duration", "")),
                preferred_pace=str(getattr(style_raw, "preferred_pace", "")),
                quality_sensitivity=float(getattr(style_raw, "quality_sensitivity", 0.5)),
                humor_preference=float(getattr(style_raw, "humor_preference", 0.5)),
                depth_preference=float(getattr(style_raw, "depth_preference", 0.5)),
            )
        ctx_raw = getattr(prefs, "context", None)
        ctx_out = ContextModeOut()
        if ctx_raw is not None:
            ctx_out = ContextModeOut(
                weekday_patterns=str(getattr(ctx_raw, "weekday_patterns", "")),
                weekend_patterns=str(getattr(ctx_raw, "weekend_patterns", "")),
                time_of_day_patterns=str(getattr(ctx_raw, "time_of_day_patterns", "")),
                session_type=str(getattr(ctx_raw, "session_type", "")),
            )

        exploration_openness = float(getattr(prefs, "exploration_openness", 0.5))

        # ── Cognition updates ──
        cognition_updates = []
        has_more_cognition_updates = False
        next_cognition_cursor = ""
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        if callable(load_cognition_updates):
            raw_updates = [
                item
                for item in load_cognition_updates()
                if isinstance(item, dict) and str(item.get("summary", "")).strip()
            ]
            raw_updates.sort(key=lambda item: str(item.get("created_at", "")).strip(), reverse=True)
            raw_updates.sort(key=lambda item: bool(item.get("notified", False)))
            try:
                start = max(int(cursor), 0)
            except ValueError:
                start = 0
            end = start + limit
            sliced_updates = raw_updates[start:end]
            has_more_cognition_updates = end < len(raw_updates)
            next_cognition_cursor = str(end) if has_more_cognition_updates else ""
            cognition_updates = [_normalize_cognition_update(item) for item in sliced_updates]

        # ── Speculative interests ──
        spec_items: list[SpeculativeInterestOut] = []
        avoidance_items: list[SpeculativeAvoidanceOut] = []
        runtime_config = getattr(ctx, "config", None) or config
        try:
            spec_state = load_speculative_state(runtime_config.data_path)

            # Filter status="active" only — confirmed/rejected items are
            # technically still in spec_state.active until force_tick rotates
            # them out, but the popup should not surface them: a user who
            # clicked 喜欢 has already given their answer and expects the
            # row to disappear, not to re-render with a "已确认" tag.
            active_specs = [item for item in spec_state.active if item.status == "active"]
            for item in active_specs[:6]:
                probe_mode, challenge = _probe_metadata_for_payload(item)
                spec_items.append(
                    SpeculativeInterestOut(
                        domain=item.domain,
                        reason=item.reason,
                        confidence=item.confidence,
                        probe_mode=probe_mode,
                        challenge=challenge,
                        confirmation_count=item.confirmation_count,
                        confirmation_threshold=item.confirmation_threshold,
                        status=item.status,
                        specifics=[
                            SpeculativeSpecificOut(
                                name=s.name,
                                confirmation_count=s.confirmation_count,
                            )
                            for s in item.specifics
                            if s.name.strip()
                        ],
                    )
                )
        except Exception:
            logger.debug("Failed to load speculative state for profile summary")

        # ── Speculative avoidances ──
        try:
            avoidance_state = load_avoidance_state(runtime_config.data_path)
            active_avoidances = [item for item in avoidance_state.active if item.status == "active"]
            avoidance_items = [
                SpeculativeAvoidanceOut(
                    domain=item.domain,
                    reason=item.reason,
                    confidence=item.confidence,
                    source_mode=item.source_mode,
                    source_signal=item.source_signal,
                    confirmation_count=item.confirmation_count,
                    confirmation_threshold=item.confirmation_threshold,
                    status=item.status,
                    specifics=[
                        SpeculativeSpecificOut(
                            name=s.name,
                            confirmation_count=s.confirmation_count,
                        )
                        for s in item.specifics
                        if s.name.strip()
                    ],
                )
                for item in active_avoidances[:6]
            ]
        except Exception:
            logger.debug("Failed to load avoidance state for profile summary")

        active_insights_out = [
            InsightHypothesisOut(
                hypothesis=str(getattr(ins, "hypothesis", "")),
                evidence=[str(e) for e in getattr(ins, "evidence", [])],
                confidence=float(getattr(ins, "confidence", 0.5)),
                validated=bool(getattr(ins, "validated", False)),
                created_at=str(getattr(ins, "created_at", "")),
            )
            for ins in getattr(profile, "active_insights", [])[:6]
            if str(getattr(ins, "hypothesis", "")).strip()
        ]

        recent_awareness_out = [
            AwarenessNoteOut(
                date=str(getattr(note, "date", "")),
                observation=str(getattr(note, "observation", "")),
                trend=str(getattr(note, "trend", "")),
                emotion_guess=str(getattr(note, "emotion_guess", "")),
            )
            for note in getattr(profile, "recent_awareness", [])[:8]
            if str(getattr(note, "observation", "")).strip()
        ]

        return ProfileSummaryResponse(
            initialized=True,
            personality_portrait=profile.personality_portrait,
            # Core
            core_traits=_cap_keeping_user_added(
                profile.core_traits, _added_list("core.core_traits"), 6
            ),
            deep_needs=_cap_keeping_user_added(
                profile.deep_needs, _added_list("core.deep_needs"), 5
            ),
            mbti=mbti_out,
            # Values
            values=_cap_keeping_user_added(
                list(getattr(profile, "values", [])), _added_list("values_layer.values"), 5
            ),
            motivational_drivers=_cap_keeping_user_added(
                list(getattr(profile, "motivational_drivers", [])),
                _added_list("values_layer.motivational_drivers"),
                4,
            ),
            # Interest
            likes=likes_out,
            dislikes=dislikes_out,
            favorite_up_users=favorite_ups,
            # Role
            life_stage=str(getattr(profile, "life_stage", "")),
            current_phase=str(getattr(profile, "current_phase", "")),
            # Surface
            cognitive_style=_cap_keeping_user_added(
                list(getattr(profile, "cognitive_style", [])),
                _added_list("surface.cognitive_style"),
                5,
            ),
            style=style_out,
            context=ctx_out,
            exploration_openness=exploration_openness,
            # Cross-cutting
            speculative_interests=spec_items,
            speculative_avoidances=avoidance_items,
            recent_cognition_updates=cognition_updates,
            has_more_cognition_updates=has_more_cognition_updates,
            next_cognition_cursor=next_cognition_cursor,
            active_insights=active_insights_out,
            recent_awareness=recent_awareness_out,
            overrides=overrides_summary,
        )

    @app.get("/api/profile/edit-state")
    async def profile_edit_state() -> dict[str, object]:
        """Full (un-truncated) editable profile + overrides + drift.

        The edit UI must use this rather than ``/api/profile-summary`` — the
        latter truncates lists for display, so it cannot reach e.g. the 13th
        interest or 9th UP.
        """
        from openbiliclaw.soul.overrides import build_edit_state

        try:
            raw = await ctx.soul_engine.get_raw_profile()
            effective = await ctx.soul_engine.get_profile()
        except Exception:
            return {"initialized": False}
        return build_edit_state(raw, effective, ctx.soul_engine.get_overrides())

    @app.post("/api/profile/edit")
    async def profile_edit(payload: ProfileEditIn) -> dict[str, object]:
        """Apply one deterministic user edit to the profile overlay.

        Returns the fresh edit-state inline so the client re-renders without
        a second round-trip. Embedding / LLM services for the dislike pool
        purge are resolved inside ``apply_user_edit`` from the soul engine.
        """
        from openbiliclaw.soul.overrides import ProfileEditError, build_edit_state

        try:
            await ctx.soul_engine.apply_user_edit(
                target=payload.target,
                op=payload.op,
                value=payload.value,
                parent=payload.parent,
                weight=payload.weight,
                database=ctx.database,
            )
        except ProfileEditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _invalidate_recommendation_snapshot()

        try:
            raw = await ctx.soul_engine.get_raw_profile()
            effective = await ctx.soul_engine.get_profile()
            edit_state: dict[str, object] = build_edit_state(
                raw, effective, ctx.soul_engine.get_overrides()
            )
        except Exception:
            edit_state = {"initialized": False}
        return {"ok": True, "target": payload.target, "op": payload.op, "edit_state": edit_state}

    @app.post("/api/events", response_model=EventIngestResponse)
    async def ingest_events(payload: BehaviorEventBatchIn) -> EventIngestResponse:
        from openbiliclaw.sources.event_format import build_event

        if _health_profile_ready() is False:
            return EventIngestResponse(
                accepted=0,
                rejected=[
                    EventRejectedOut(
                        index=index,
                        type=str(item.type or "").strip(),
                        reason="not_initialized",
                    )
                    for index, item in enumerate(payload.events)
                ],
            )

        canonical_events: list[dict[str, Any]] = []
        for item in payload.events:
            raw_source_platform = str(item.source_platform or "").strip()
            raw_event_type = str(item.type or "").strip()
            event_type = "feedback" if raw_event_type == "dislike" else raw_event_type
            # Coerce context to a string for downstream LLM consumers.
            # Pre-v0.3.22 this passed item.context through verbatim — when
            # the extension sent a dict (e.g. structured click context),
            # database serialization stored it as a JSON blob and prompt
            # builders surfaced "[object Object]"-like noise. build_event
            # fills in a natural-language fallback when context is empty
            # / non-string.
            raw_context = item.context
            if isinstance(raw_context, str):
                context_str = raw_context.strip()
            elif raw_context is None:
                context_str = ""
            else:
                # Dict / list / other — fold into metadata so it's
                # preserved without polluting the LLM-facing context.
                context_str = ""
            metadata = {
                **item.metadata,
                "timestamp": item.timestamp,
            }
            if raw_event_type == "dislike":
                metadata.setdefault("feedback_type", "dislike")
                metadata.setdefault("reaction", "thumbs_down")
            feedback_type = str(metadata.get("feedback_type") or "").strip().lower()
            if event_type == "feedback" and feedback_type == "retraction":
                metadata.setdefault("event_namespace", "retraction")
                metadata["profile_update_owner"] = "generic"
            elif event_type == "feedback" and feedback_type in {
                "like",
                "dislike",
                "comment",
                "dismiss",
            }:
                metadata.setdefault("event_namespace", "content")
                metadata["profile_update_owner"] = "content_feedback"
            else:
                metadata.setdefault("event_namespace", "content")
                metadata["profile_update_owner"] = "generic"
            if not isinstance(raw_context, str) and raw_context:
                metadata.setdefault("raw_context", raw_context)
            # v0.3.x event-satisfaction: fold top-level dwell into
            # metadata so the storage classifier sees them in one place.
            # `setdefault` preserves an explicit metadata.watch_seconds
            # the extension might already have set inside metadata.
            if item.watch_seconds is not None:
                metadata.setdefault("watch_seconds", item.watch_seconds)
            if item.video_duration_seconds is not None:
                metadata.setdefault("video_duration_seconds", item.video_duration_seconds)
            event = build_event(
                event_type=event_type,
                # Leave source resolution to the shared event formatter.  In
                # particular, an old payload without a platform must still
                # allow a YouTube/X/etc. URL to win before the B站 fallback.
                source_platform=raw_source_platform,
                legacy_platform="bilibili",
                title=item.title or "",
                url=item.url or "",
                author=str(metadata.get("author", "") or metadata.get("up_name", "") or ""),
                context=context_str,
                metadata=metadata,
            )
            event["ingest_key"] = item.event_id.strip()
            canonical_events.append(event)
        receipt = await event_ingress.accept_batch(
            canonical_events,
            producer="extension",
        )
        # A meaningful V2EX Topic read also feeds the account-scoped Node
        # affinity projection. Event persistence is the durability fence: if
        # this projection fails, the request fails and an extension retry gets
        # the same durable event receipt, then repairs the idempotent Topic
        # projection without duplicating either row. Never project into an
        # active profile while the browser is observed as another account.
        active_v2ex_username = ""
        if hasattr(ctx.database, "get_v2ex_profile_identity"):
            active_v2ex_username = str(ctx.database.get_v2ex_profile_identity()[0] or "").strip()
        observed_v2ex_username = ""
        if hasattr(ctx.database, "get_v2ex_browser_identity"):
            observed_v2ex_username = str(ctx.database.get_v2ex_browser_identity()[0] or "").strip()
        from openbiliclaw.sources.v2ex_affinity import v2ex_affinity_projection_username

        affinity_username = v2ex_affinity_projection_username(
            active_v2ex_username,
            observed_v2ex_username,
        )
        if affinity_username:
            from openbiliclaw.sources.v2ex_affinity import (
                V2EXNodeAffinityStore,
                v2ex_engaged_view_affinity_item,
            )

            engaged_view_items = [
                affinity_item
                for item_receipt in receipt.items
                if not item_receipt.error
                and (item_receipt.inserted or item_receipt.duplicate)
                and (
                    affinity_item := v2ex_engaged_view_affinity_item(
                        canonical_events[item_receipt.index],
                        event_id=item_receipt.event_id,
                    )
                )
                is not None
            ]
            if engaged_view_items:
                V2EXNodeAffinityStore(ctx.database).record_items(
                    engaged_view_items,
                    username=affinity_username,
                )
        if receipt.inserted > 0:
            await _request_runtime_replenishment(reason="event_ingest")
        # Notify popup that the activity feed has new entries so it can
        # refresh its UI without polling. Throttled naturally to once per
        # ingest call (extension batches 10+ events into a single POST).
        if receipt.inserted > 0:
            event_hub = getattr(ctx.runtime_controller, "event_hub", None)
            publish = getattr(event_hub, "publish", None)
            if callable(publish):
                with suppress(Exception):
                    await publish(
                        {
                            "type": "activity.added",
                            "count": receipt.inserted,
                        }
                    )
        return EventIngestResponse(
            accepted=receipt.accepted,
            duplicates=receipt.duplicates,
            rejected=[
                EventRejectedOut(
                    index=item.index,
                    type=item.event_type,
                    reason=item.error,
                )
                for item in receipt.items
                if item.error
            ],
            receipts=[
                EventReceiptOut(
                    index=item.index,
                    event_id=item.event_id,
                    event_type=item.event_type,
                    inserted=item.inserted,
                    duplicate=item.duplicate,
                )
                for item in receipt.items
                if not item.error
            ],
        )

    async def _load_recommendations(
        disliked_topics: list[str] | None = None,
    ) -> tuple[RecommendationListResponse, float]:
        nonlocal first_page_topup_attempted_at

        def _admission_min_score() -> float:
            runtime_config = getattr(ctx, "config", None) or config
            discovery_config = getattr(runtime_config, "discovery", None)
            try:
                threshold = float(getattr(discovery_config, "admission_min_score", 0.60) or 0.60)
            except (TypeError, ValueError):
                return 0.60
            return threshold if 0.0 < threshold <= 1.0 else 0.60

        def _filter_low_confidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            threshold = _admission_min_score()
            filtered: list[dict[str, Any]] = []
            for row in rows:
                if "confidence" not in row:
                    filtered.append(row)
                    continue
                try:
                    confidence = float(row.get("confidence") or 0.0)
                except (TypeError, ValueError):
                    confidence = 0.0
                if confidence >= threshold:
                    filtered.append(row)
            return filtered

        # Pull a 2x window so the per-franchise cap below still has 20
        # survivors to return after dropping over-represented IPs.
        # Without the wider pool, capping 原神 at 2 in a 20-row request
        # would leave gaps that other items further back in time would
        # have filled.
        rows = _filter_low_confidence(
            ctx.database.get_recommendations(limit=40, exclude_processed=True)
        )

        # Fresh-install bootstrap + thin-first-page top-up (issue #81):
        # ``recommendations`` table is the write-only history of items we've
        # ever served. On first popup load nobody has called ``reshuffle`` /
        # ``append`` / CLI ``recommend`` yet, so the table is empty even if
        # the discovery pool already has 100+ scored candidates. And after a
        # session of feedback the unprocessed window can shrink to 2-3 rows —
        # a "挤牙膏" first page — while the pool still has stock. In both
        # cases surface pool candidates by bootstrapping a ``serve()`` call
        # right here — it writes up to 10 fresh entries to the history table
        # that the next ``rows = get_recommendations`` re-read will pick up.
        # Failure is fully silent: any error returns the original list,
        # leaving the popup's "正在补货" state intact and giving the regular
        # refresh tick another chance. The non-empty case is debounced so a
        # filtered-out pool cannot make polling clients re-serve every tick.
        # gui-init D1: this bootstrap calls serve(), which WRITES
        # (recommendation rows + pool "shown" markers). It's a side-effecting
        # GET, so the deny-by-default middleware (POST/PUT/PATCH/DELETE) doesn't
        # cover it — skip it during an active init so a read can't serve from /
        # mark a half-built pool. The post-init refresh tick serves normally.
        if (
            len(rows) < _FIRST_PAGE_TOPUP_FLOOR
            and not _init_active_now()
            and ctx.recommendation_engine is not None
            and ctx.soul_engine is not None
            and (
                not rows
                or time.monotonic() - first_page_topup_attempted_at
                >= _FIRST_PAGE_TOPUP_DEBOUNCE_SECONDS
            )
        ):
            with suppress(Exception):
                pool_count_fn = getattr(ctx.database, "count_pool_candidates", None)
                pool_count = int(pool_count_fn()) if callable(pool_count_fn) else 0
                if pool_count > 0:
                    first_page_topup_attempted_at = time.monotonic()
                    row_count_before = len(rows)
                    profile = await ctx.soul_engine.get_profile()
                    await ctx.recommendation_engine.serve(profile, limit=10)
                    rows = _filter_low_confidence(
                        ctx.database.get_recommendations(limit=40, exclude_processed=True)
                    )
                    await _publish_pool_status_snapshot()
                    logger.info(
                        "GET /api/recommendations top-up: history had %d "
                        "unprocessed row(s) (pool_count=%d → now %d rows)",
                        row_count_before,
                        pool_count,
                        len(rows),
                    )

        if disliked_topics:
            from openbiliclaw.recommendation.exclusion import filter_recommendation_rows

            rows = filter_recommendation_rows(rows, disliked_topics)
        rows, snapshot_expires_at = _recommendation_snapshot_rows_and_expiry(rows)
        rows = _cap_by_franchise(rows, max_per_franchise=2)[:20]

        return RecommendationListResponse(
            items=[
                RecommendationOut(
                    id=int(row["id"]),
                    bvid=str(row.get("bvid", "")),
                    item_key=str(row.get("item_key", "")),
                    title=str(row.get("title", "")),
                    up_name=str(row.get("up_name", "")),
                    cover_url=str(row.get("cover_url", "")),
                    expression=str(row.get("expression", "")),
                    topic_label=str(row.get("topic", "")),
                    presented=bool(row.get("presented", 0)),
                    feedback_type=str(row.get("feedback_type", "") or ""),
                    content_id=str(row.get("content_id", "") or row.get("bvid", "")),
                    content_url=str(row.get("content_url", "") or ""),
                    source_platform=str(row.get("source_platform", "") or "bilibili"),
                    published_at=str(row.get("published_at", "") or ""),
                    published_label=str(row.get("published_label", "") or ""),
                    content_type=str(row.get("content_type", "") or "video"),
                    body_text=str(row.get("body_text", "") or ""),
                    source_metadata=_fail_closed_source_metadata(row.get("source_metadata", {})),
                    duration=int(row.get("duration", 0) or 0),
                    view_count=int(row.get("view_count", 0) or 0),
                    like_count=int(row.get("like_count", 0) or 0),
                    danmaku_count=int(row.get("danmaku_count", 0) or 0),
                    favorite_count=int(
                        row.get("favorite_count", 0) or row.get("collect_count", 0) or 0
                    ),
                    comment_count=int(row.get("comment_count", 0) or 0),
                    share_count=int(row.get("share_count", 0) or 0),
                    rating_score=float(row.get("rating_score", 0.0) or 0.0),
                    rating_count=int(row.get("rating_count", 0) or 0),
                    source_rank=int(row.get("source_rank", 0) or 0),
                    up_mid=int(row.get("up_mid", 0) or 0),
                )
                for row in rows
            ]
        ), snapshot_expires_at

    @app.get("/api/recommendations", response_model=RecommendationListResponse)
    async def recommendations() -> RecommendationListResponse:
        """Return one coalesced recommendation snapshot.

        Restored browser sessions can contain dozens of stale dashboard tabs.
        When the backend comes back, those tabs all issue the same expensive
        history/content join at once. A one-second cache plus single-flight
        lock turns that burst into one database read while keeping interactive
        mutations immediately visible through explicit invalidation.
        """
        nonlocal recommendation_snapshot_cache, recommendation_snapshot_cached_at
        nonlocal recommendation_snapshot_expires_at
        nonlocal recommendation_snapshot_dislike_digest

        now = time.monotonic()
        disliked_topics, dislike_digest = _effective_recommendation_dislikes()
        if (
            recommendation_snapshot_cache is not None
            and now - recommendation_snapshot_cached_at < _RECOMMENDATION_SNAPSHOT_TTL_SECONDS
            and now < recommendation_snapshot_expires_at
            and recommendation_snapshot_dislike_digest == dislike_digest
        ):
            return recommendation_snapshot_cache.model_copy(deep=True)

        async with recommendation_snapshot_lock:
            now = time.monotonic()
            disliked_topics, dislike_digest = _effective_recommendation_dislikes()
            if (
                recommendation_snapshot_cache is not None
                and now - recommendation_snapshot_cached_at < _RECOMMENDATION_SNAPSHOT_TTL_SECONDS
                and now < recommendation_snapshot_expires_at
                and recommendation_snapshot_dislike_digest == dislike_digest
            ):
                return recommendation_snapshot_cache.model_copy(deep=True)
            snapshot, snapshot_expires_at = await _load_recommendations(disliked_topics)
            latest_topics, latest_digest = _effective_recommendation_dislikes()
            if latest_digest != dislike_digest:
                snapshot, snapshot_expires_at = await _load_recommendations(latest_topics)
                dislike_digest = latest_digest
            recommendation_snapshot_cache = snapshot.model_copy(deep=True)
            recommendation_snapshot_cached_at = time.monotonic()
            recommendation_snapshot_expires_at = snapshot_expires_at
            recommendation_snapshot_dislike_digest = dislike_digest
            return snapshot

    def _content_history_item(row: dict[str, Any]) -> ContentHistoryItemOut:
        source_platform = _normalize_source_platform(
            str(row.get("source_platform", "") or "bilibili")
        )
        content_id = str(row.get("content_id", "") or "").strip()
        content_url = _normalize_content_history_http_url(row.get("content_url", ""))
        if not content_url:
            content_url = _normalize_content_history_http_url(
                _fallback_recommendation_click_url(
                    source_platform=source_platform,
                    content_id=content_id,
                    bvid=content_id,
                )
            )
        cover_url = _normalize_content_history_http_url(row.get("cover_url", ""))
        contexts: list[ContentHistoryContextOut] = []
        raw_contexts = row.get("contexts", [])
        if isinstance(raw_contexts, list):
            for raw_context in raw_contexts:
                if not isinstance(raw_context, Mapping):
                    continue
                context = str(raw_context.get("context", "") or "")
                occurred_at = str(raw_context.get("occurred_at", "") or "")
                if context not in {"favorite", "watch_later", "dismiss", "dislike"}:
                    continue
                contexts.append(
                    ContentHistoryContextOut(
                        context=cast("Any", context),
                        occurred_at=occurred_at,
                        restored=bool(raw_context.get("restored", False)),
                    )
                )
        recommendation_id = row.get("recommendation_id")
        return ContentHistoryItemOut(
            item_key=str(row.get("item_key", "") or ""),
            source_platform=source_platform,
            content_id=content_id,
            content_url=content_url,
            content_type=str(row.get("content_type", "") or "video"),
            title=str(row.get("title", "") or ""),
            author_name=str(row.get("author_name", "") or ""),
            cover_url=cover_url,
            body_text=str(row.get("body_text", "") or ""),
            recommendation_id=(int(recommendation_id) if recommendation_id is not None else None),
            occurred_at=str(row.get("occurred_at", "") or ""),
            context=str(row.get("context", "") or ""),
            restored=bool(row.get("restored", False)),
            contexts=contexts,
        )

    @app.get("/api/content-history", response_model=ContentHistoryResponse)
    async def content_history(
        category: Literal["clicked", "shown", "removed"] = Query(...),
        limit: int = Query(default=12, ge=1, le=50),
        offset: int | None = Query(
            default=None,
            ge=0,
            le=_SQLITE_SIGNED_INTEGER_MAX,
        ),
        cursor: str | None = Query(
            default=None,
            min_length=1,
            max_length=_CONTENT_HISTORY_CURSOR_MAX_LENGTH,
        ),
    ) -> ContentHistoryResponse:
        """Return one lazy-loadable category from the bounded content history."""
        if cursor is not None and offset is not None:
            raise HTTPException(
                status_code=422,
                detail="content history cursor cannot be combined with offset",
            )
        try:
            cursor_position = (
                _decode_content_history_cursor(cursor, category=category)
                if cursor is not None
                else None
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        rows, total, has_more, next_position = await asyncio.to_thread(
            ctx.database.list_content_history_page,
            category,
            limit=limit,
            offset=offset or 0,
            cursor=cursor_position,
            retention_days=CONTENT_HISTORY_RETENTION_DAYS,
        )
        next_cursor = (
            _encode_content_history_cursor(category, next_position)
            if has_more and next_position is not None
            else None
        )
        return ContentHistoryResponse(
            category=category,
            items=[_content_history_item(row) for row in rows],
            total=total,
            retention_days=CONTENT_HISTORY_RETENTION_DAYS,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    # ── Platform-neutral saved memberships and native sync ─────────

    saved_state_snapshot_cache: dict[
        tuple[SavedListKind, str], tuple[float, SavedItemStateResponse]
    ] = {}

    def _invalidate_saved_state(list_kind: SavedListKind, item_key: str) -> None:
        saved_state_snapshot_cache.pop((list_kind, item_key), None)

    def _saved_service() -> Any:
        service = getattr(ctx, "saved_sync_service", None)
        if service is None:
            raise HTTPException(status_code=503, detail="saved sync service unavailable")
        return service

    def _saved_count(list_kind: SavedListKind) -> int:
        if list_kind == "favorite":
            return int(ctx.database.count_favorites())
        return int(ctx.database.count_watch_later())

    def _safe_native_status(value: object) -> NativeSaveStatus:
        if isinstance(value, str):
            for status in NATIVE_SAVE_STATUSES:
                if value == status:
                    return status
        return "failed"

    def _safe_result_text(value: object, *, limit: int = 512) -> str:
        if not isinstance(value, str):
            return ""
        filtered = "".join(
            character for character in value if not unicodedata.category(character).startswith("C")
        )
        return filtered[:limit]

    def _saved_state_response(
        list_kind: SavedListKind,
        item_key: str,
    ) -> SavedItemStateResponse:
        cache_key = (list_kind, item_key)
        cached = saved_state_snapshot_cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < _RECOMMENDATION_SNAPSHOT_TTL_SECONDS:
            return cached[1].model_copy(deep=True)
        row = ctx.database.get_saved_membership(list_kind, item_key)
        if row is None:
            response = SavedItemStateResponse(saved=False, item_key=item_key)
        else:
            response = SavedItemStateResponse(
                saved=True,
                item_key=item_key,
                sync_status=_safe_native_status(row.get("sync_status")),
                sync_task_id=str(row.get("sync_task_id", "")),
                resolved_action=str(row.get("resolved_action", "")),
                resolved_target=_safe_result_text(row.get("resolved_target", ""), limit=256),
                error_code=_safe_result_text(row.get("last_error_code", ""), limit=128),
                error_message=_safe_result_text(row.get("last_error_message", "")),
            )
        if len(saved_state_snapshot_cache) >= 1000:
            saved_state_snapshot_cache.clear()
        saved_state_snapshot_cache[cache_key] = (now, response.model_copy(deep=True))
        return response

    def _saved_list_item(row: dict[str, Any]) -> SavedListItem:
        return SavedListItem(
            item_key=str(row.get("item_key", "")),
            source_platform=str(row.get("source_platform", "")),
            content_id=str(row.get("content_id", "")),
            content_url=str(row.get("content_url", "")),
            content_type=str(row.get("content_type", "") or "video"),
            title=str(row.get("title", "")),
            author_name=str(row.get("author_name", "")),
            cover_url=str(row.get("cover_url", "")),
            note=str(row.get("note", "")),
            added_at=str(row.get("added_at", "")),
            sync_status=_safe_native_status(row.get("sync_status")),
            sync_task_id=str(row.get("sync_task_id", "")),
            requested_action=str(row.get("requested_action", "")),
            resolved_action=str(row.get("resolved_action", "")),
            resolved_target=_safe_result_text(row.get("resolved_target", ""), limit=256),
            error_code=_safe_result_text(row.get("last_error_code", ""), limit=128),
            error_message=_safe_result_text(row.get("last_error_message", "")),
        )

    def _sync_item_response(result: NativeSaveResult) -> SavedSyncItemResponse:
        return SavedSyncItemResponse(
            item_key=result.item_key,
            status=_safe_native_status(result.status),
            resolved_action=result.resolved_action,
            resolved_target=_safe_result_text(result.resolved_target, limit=256),
            error_code=_safe_result_text(result.error_code, limit=128),
            error_message=_safe_result_text(result.error_message),
        )

    def _sync_batch_response(result: SavedSyncBatchResult) -> SavedSyncBatchResponse:
        return SavedSyncBatchResponse(
            task_id=result.task_id,
            items=[_sync_item_response(item) for item in result.items],
        )

    @app.post("/api/saved/{list_kind}", response_model=SavedItemStateResponse)
    async def saved_add(
        list_kind: SavedListKind,
        payload: SavedItemIn,
    ) -> SavedItemStateResponse:
        item = SavedItemInput(
            source_platform=payload.source_platform,
            content_id=payload.content_id,
            content_url=payload.content_url,
            content_type=payload.content_type,
            title=payload.title,
            author_name=payload.author_name,
            cover_url=payload.cover_url,
        )
        saved_sync = getattr(getattr(ctx, "config", None), "saved_sync", None)
        auto_sync = bool(getattr(saved_sync, "auto_sync_enabled", False))
        try:
            result = _saved_service().save_local(
                list_kind,
                item,
                note=payload.note,
                auto_sync=auto_sync,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid saved item") from exc
        _invalidate_saved_state(list_kind, result.item_key)
        if result.sync_status == "pending" and result.sync_task_id:
            return SavedItemStateResponse(
                saved=result.saved,
                item_key=result.item_key,
                sync_status="pending",
                sync_task_id=result.sync_task_id,
            )
        if result.sync_task_id:
            try:
                created = _saved_service().get_sync_task(result.sync_task_id)
            except (AttributeError, ValueError):  # pragma: no cover - compatibility injection
                created = SavedSyncBatchResult(task_id=result.sync_task_id, items=())
            created_item = next(
                (item for item in created.items if item.item_key == result.item_key),
                None,
            )
            if created_item is not None:
                item_response = _sync_item_response(created_item)
                return SavedItemStateResponse(
                    saved=result.saved,
                    item_key=result.item_key,
                    sync_status=item_response.status,
                    sync_task_id=result.sync_task_id,
                    resolved_action=item_response.resolved_action,
                    resolved_target=item_response.resolved_target,
                    error_code=item_response.error_code,
                    error_message=item_response.error_message,
                )
        state = _saved_state_response(list_kind, result.item_key)
        return state.model_copy(
            update={
                "saved": result.saved,
                "sync_status": result.sync_status,
                "sync_task_id": result.sync_task_id,
            }
        )

    @app.post("/api/saved/{list_kind}/remove", response_model=SavedItemStateResponse)
    async def saved_remove(
        list_kind: SavedListKind,
        payload: SavedItemKeyIn,
    ) -> SavedItemStateResponse:
        ctx.database.remove_saved_membership(list_kind, payload.item_key)
        _invalidate_saved_state(list_kind, payload.item_key)
        return _saved_state_response(list_kind, payload.item_key)

    @app.get("/api/saved/{list_kind}/status", response_model=SavedItemStateResponse)
    async def saved_status(
        list_kind: SavedListKind,
        item_key: str = Query(...),
    ) -> SavedItemStateResponse:
        try:
            normalized_key = validate_saved_item_key(item_key)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid item_key") from exc
        return _saved_state_response(list_kind, normalized_key)

    @app.get("/api/saved/{list_kind}", response_model=SavedListResponse)
    async def saved_list(
        list_kind: SavedListKind,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> SavedListResponse:
        rows = ctx.database.list_saved_memberships(list_kind, limit=limit, offset=offset)
        return SavedListResponse(
            items=[_saved_list_item(row) for row in rows],
            total=_saved_count(list_kind),
        )

    @app.post("/api/saved/{list_kind}/sync", response_model=SavedSyncBatchResponse)
    async def saved_sync(
        list_kind: SavedListKind,
        payload: SavedSyncRequest,
    ) -> SavedSyncBatchResponse:
        trigger = "manual_single" if len(payload.item_keys) == 1 else "manual_batch"
        try:
            created = _saved_service().create_sync_task(
                list_kind,
                payload.item_keys,
                trigger,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid sync selection") from exc
        return _sync_batch_response(created)

    @app.get("/api/saved-sync/tasks/{task_id}", response_model=SavedSyncBatchResponse)
    async def saved_sync_task(task_id: UUID) -> SavedSyncBatchResponse:
        service = _saved_service()
        try:
            exists = service.has_sync_task(str(task_id))
            result = service.get_sync_task(str(task_id)) if exists else None
        except ValueError as exc:  # pragma: no cover - UUID path validation protects this
            raise HTTPException(status_code=422, detail="invalid task_id") from exc
        if result is None:
            raise HTTPException(status_code=404, detail="saved sync task not found")
        return _sync_batch_response(result)

    # ── Legacy Bilibili watch-later (稍后再看) ─────────────────────

    def _legacy_saved_state(
        list_kind: SavedListKind,
        bvid: str,
    ) -> tuple[bool, str, NativeSaveStatus | None, str, str, str, str, str]:
        normalized_bvid = bvid.strip()
        if not normalized_bvid:
            raise HTTPException(status_code=422, detail="bvid is required")
        item_key = make_item_key("bilibili", normalized_bvid)
        row = ctx.database.get_saved_membership(list_kind, item_key)
        return (
            row is not None,
            item_key,
            _safe_native_status(row.get("sync_status")) if row is not None else None,
            str(row.get("sync_task_id", "")) if row is not None else "",
            str(row.get("resolved_action", "")) if row is not None else "",
            _safe_result_text(row.get("resolved_target", ""), limit=256) if row is not None else "",
            _safe_result_text(row.get("last_error_code", ""), limit=128) if row is not None else "",
            _safe_result_text(row.get("last_error_message", "")) if row is not None else "",
        )

    def _watch_later_state(bvid: str) -> WatchLaterStateResponse:
        (
            saved,
            item_key,
            sync_status,
            sync_task_id,
            resolved_action,
            resolved_target,
            error_code,
            error_message,
        ) = _legacy_saved_state("watch_later", bvid)
        return WatchLaterStateResponse(
            saved=saved,
            total=ctx.database.count_watch_later(),
            item_key=item_key,
            sync_status=sync_status,
            sync_task_id=sync_task_id,
            resolved_action=resolved_action,
            resolved_target=resolved_target,
            error_code=error_code,
            error_message=error_message,
        )

    @app.post("/api/watch-later", response_model=WatchLaterStateResponse)
    async def watch_later_add(payload: WatchLaterAddIn) -> WatchLaterStateResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="bvid is required")
        _saved_service().save_local(
            "watch_later",
            SavedItemInput("bilibili", bvid),
            note=payload.note.strip(),
            auto_sync=False,
        )
        # Keep the compatibility table and cached metadata snapshot aligned.
        ctx.database.add_to_watch_later(bvid, note=payload.note.strip())
        return _watch_later_state(bvid)

    @app.delete("/api/watch-later/{bvid}", response_model=WatchLaterStateResponse)
    async def watch_later_remove(bvid: str) -> WatchLaterStateResponse:
        normalized = bvid.strip()
        ctx.database.remove_from_watch_later(normalized)
        return _watch_later_state(normalized)

    @app.get("/api/watch-later/{bvid}", response_model=WatchLaterStateResponse)
    async def watch_later_status(bvid: str) -> WatchLaterStateResponse:
        return _watch_later_state(bvid.strip())

    @app.get("/api/watch-later", response_model=WatchLaterListResponse)
    async def watch_later_list(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> WatchLaterListResponse:
        rows = ctx.database.list_saved_memberships("watch_later", limit=limit, offset=offset)
        return WatchLaterListResponse(
            items=[
                WatchLaterItem(
                    bvid=str(row.get("content_id", "")),
                    item_key=str(row.get("item_key", "")),
                    content_id=str(row.get("content_id", "") or row.get("bvid", "")),
                    title=str(row.get("title", "")),
                    up_name=str(row.get("author_name", "")),
                    cover_url=str(row.get("cover_url", "")),
                    content_url=str(row.get("content_url", "")),
                    source_platform=str(row.get("source_platform", "") or "bilibili"),
                    content_type=str(row.get("content_type", "") or "video"),
                    added_at=str(row.get("added_at", "")),
                    sync_status=_safe_native_status(row.get("sync_status")),
                    sync_task_id=str(row.get("sync_task_id", "")),
                    resolved_action=str(row.get("resolved_action", "")),
                    resolved_target=_safe_result_text(row.get("resolved_target", ""), limit=256),
                    error_code=_safe_result_text(row.get("last_error_code", ""), limit=128),
                    error_message=_safe_result_text(row.get("last_error_message", "")),
                )
                for row in rows
            ],
            total=ctx.database.count_watch_later(),
        )

    # ── Favorites (收藏夹) ────────────────────────────────────────

    def _favorite_state(bvid: str) -> FavoriteStateResponse:
        (
            saved,
            item_key,
            sync_status,
            sync_task_id,
            resolved_action,
            resolved_target,
            error_code,
            error_message,
        ) = _legacy_saved_state("favorite", bvid)
        return FavoriteStateResponse(
            saved=saved,
            total=ctx.database.count_favorites(),
            item_key=item_key,
            sync_status=sync_status,
            sync_task_id=sync_task_id,
            resolved_action=resolved_action,
            resolved_target=resolved_target,
            error_code=error_code,
            error_message=error_message,
        )

    @app.post("/api/favorites", response_model=FavoriteStateResponse)
    async def favorite_add(payload: FavoriteAddIn) -> FavoriteStateResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="bvid is required")
        _saved_service().save_local(
            "favorite",
            SavedItemInput("bilibili", bvid),
            note=payload.note.strip(),
            auto_sync=False,
        )
        # Keep the compatibility table and cached metadata snapshot aligned.
        ctx.database.add_to_favorites(bvid, note=payload.note.strip())
        return _favorite_state(bvid)

    @app.delete("/api/favorites/{bvid}", response_model=FavoriteStateResponse)
    async def favorite_remove(bvid: str) -> FavoriteStateResponse:
        normalized = bvid.strip()
        ctx.database.remove_from_favorites(normalized)
        return _favorite_state(normalized)

    @app.get("/api/favorites/{bvid}", response_model=FavoriteStateResponse)
    async def favorite_status(bvid: str) -> FavoriteStateResponse:
        return _favorite_state(bvid.strip())

    @app.get("/api/favorites", response_model=FavoriteListResponse)
    async def favorite_list(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> FavoriteListResponse:
        rows = ctx.database.list_saved_memberships("favorite", limit=limit, offset=offset)
        return FavoriteListResponse(
            items=[
                FavoriteItem(
                    bvid=str(row.get("content_id", "")),
                    item_key=str(row.get("item_key", "")),
                    content_id=str(row.get("content_id", "") or row.get("bvid", "")),
                    title=str(row.get("title", "")),
                    up_name=str(row.get("author_name", "")),
                    cover_url=str(row.get("cover_url", "")),
                    content_url=str(row.get("content_url", "")),
                    source_platform=str(row.get("source_platform", "") or "bilibili"),
                    content_type=str(row.get("content_type", "") or "video"),
                    added_at=str(row.get("added_at", "")),
                    sync_status=_safe_native_status(row.get("sync_status")),
                    sync_task_id=str(row.get("sync_task_id", "")),
                    resolved_action=str(row.get("resolved_action", "")),
                    resolved_target=_safe_result_text(row.get("resolved_target", ""), limit=256),
                    error_code=_safe_result_text(row.get("last_error_code", ""), limit=128),
                    error_message=_safe_result_text(row.get("last_error_message", "")),
                )
                for row in rows
            ],
            total=ctx.database.count_favorites(),
        )

    @app.get("/api/activity-feed", response_model=ActivityFeedResponse)
    async def activity_feed(
        limit: int = 10,
        before: str = "",
    ) -> ActivityFeedResponse:
        from openbiliclaw.runtime.activity_feed import ActivityFeedBuilder

        runtime_status: dict[str, object] = {}
        get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
        if callable(get_runtime_status):
            runtime_status = dict(get_runtime_status())
        get_account_sync_status = getattr(ctx.account_sync_service, "get_runtime_status", None)
        if callable(get_account_sync_status):
            runtime_status.update(get_account_sync_status())

        cognition_updates: list[dict[str, object]] = []
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        if callable(load_cognition_updates):
            cognition_updates = [
                item for item in load_cognition_updates() if isinstance(item, dict)
            ]

        builder = ActivityFeedBuilder(database=ctx.database)
        payload = builder.build(
            runtime_status=runtime_status,
            cognition_updates=cognition_updates,
            limit=limit,
            before=before,
        )
        payload_items = payload.get("items", [])
        item_dicts = payload_items if isinstance(payload_items, list) else []
        return ActivityFeedResponse(
            live_summary=str(payload.get("live_summary", "")),
            headline=str(payload.get("headline", "")),
            items=[
                ActivityFeedItemOut(
                    id=str(item.get("id", "")),
                    kind=str(item.get("kind", "")),
                    summary=str(item.get("summary", "")),
                    detail=str(item.get("detail", "")),
                    created_at=str(item.get("created_at", "")),
                    tone=str(item.get("tone", "info")),
                )
                for item in item_dicts
                if isinstance(item, dict)
            ],
            has_more=bool(payload.get("has_more", False)),
            next_cursor=str(payload.get("next_cursor", "")),
        )

    async def _classify_new_pool_items() -> None:
        """Legacy recovery for content_cache rows that lack content features.

        Normal source ingest writes ``discovery_candidates`` and lets the
        shared discovery-candidate pipeline evaluate/admit content before it
        reaches ``content_cache``.  This helper remains for old databases or
        explicit repair paths where rows are already cached but still missing
        ``style_key``, ``topic_group``, and ``relevance_score``.

        Silent skip when soul profile hasn't been built yet (init's first
        ~7 minutes). Otherwise events ingested before profile-ready would
        log ERROR-level traces for every batch — the legitimate retry is
        the next-tick + the profile-ready hook in ``SoulEngine``.
        """
        if ctx.recommendation_engine is None or ctx.soul_engine is None:
            return
        if not ctx.soul_engine.is_profile_ready():
            logger.debug("Background pool classification skipped: soul profile not ready")
            return
        try:
            profile = await ctx.soul_engine.get_profile()
            await ctx.recommendation_engine.classify_pool_backlog(
                profile=profile,
                limit=30,
            )
        except Exception:
            logger.exception("Background pool classification failed")

    def _notify_discovery_candidates_enqueued(source: str) -> None:
        """Wake continuous evaluation after the enqueue transaction commits."""

        coordinator = getattr(ctx.runtime_controller, "candidate_eval_coordinator", None)
        notify = getattr(coordinator, "notify", None)
        if callable(notify):
            notify(f"candidate_enqueued:{source}")
            return

        async def _drain_discovery_candidates_once() -> None:
            drain = getattr(ctx.runtime_controller, "drain_discovery_candidates_once", None)
            if not callable(drain):
                return
            try:
                await drain(batch_size=30)
            except Exception:
                logger.exception("Background discovery candidate drain failed")

        asyncio.create_task(_drain_discovery_candidates_once())

    def _pool_available_count() -> int | None:
        """Return the best available servable-pool count for hot-path guards."""

        def _sync_inventory(available: int) -> int:
            update = getattr(ctx.llm_concurrency_gate, "update_inventory", None)
            if callable(update):
                update(
                    available=available,
                    target=_inventory_target(),
                )
            return available

        get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
        if callable(get_runtime_status):
            with suppress(Exception):
                status = get_runtime_status()
                if isinstance(status, dict) and "pool_available_count" in status:
                    return _sync_inventory(max(0, int(status.get("pool_available_count") or 0)))

        readiness = getattr(ctx.database, "count_pool_readiness", None)
        if callable(readiness):
            with suppress(Exception):
                counts = readiness()
                if isinstance(counts, dict) and "available" in counts:
                    return _sync_inventory(max(0, int(counts.get("available") or 0)))

        count_pool = getattr(ctx.database, "count_pool_candidates", None)
        if callable(count_pool):
            with suppress(Exception):
                return _sync_inventory(max(0, int(count_pool())))
        return None

    def _runtime_pool_status_payload() -> dict[str, object]:
        """Return frontend runtime fields needed to resync pool status."""
        status: dict[str, object] = {}
        get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
        if callable(get_runtime_status):
            with suppress(Exception):
                runtime_status = get_runtime_status()
                if isinstance(runtime_status, dict):
                    status.update(runtime_status)

        if "pool_available_count" not in status:
            readiness = getattr(ctx.database, "count_pool_readiness", None)
            if callable(readiness):
                with suppress(Exception):
                    counts = readiness()
                    if isinstance(counts, dict):
                        status.update(
                            {
                                "pool_available_count": counts.get("available", 0),
                                "pool_raw_count": counts.get("raw", counts.get("available", 0)),
                                "pool_pending_count": counts.get("pending", 0),
                                "pool_pending_eval_count": counts.get("pending_eval", 0),
                                "pool_evaluated_pending_count": counts.get("evaluated_pending", 0),
                            }
                        )
            else:
                count_pool = getattr(ctx.database, "count_pool_candidates", None)
                if callable(count_pool):
                    with suppress(Exception):
                        status["pool_available_count"] = int(count_pool())

        int_fields = (
            "pool_available_count",
            "pool_raw_count",
            "pool_pending_count",
            "pool_pending_eval_count",
            "pool_evaluated_pending_count",
            "pool_target_count",
            "last_replenished_count",
            "last_discovered_count",
        )
        payload: dict[str, object] = {}
        for field in int_fields:
            if field not in status:
                continue
            raw_value = status.get(field)
            if raw_value is None:
                raw_value = 0
            with suppress(TypeError, ValueError):
                payload[field] = max(0, int(cast("Any", raw_value)))
        recent_pool_topics = status.get("recent_pool_topics")
        if isinstance(recent_pool_topics, list):
            payload["recent_pool_topics"] = [
                str(item) for item in recent_pool_topics if str(item).strip()
            ]
        return payload

    async def _publish_pool_status_snapshot(
        counts: dict[str, int] | None = None,
        message: str = "推荐池已同步",
    ) -> None:
        """Broadcast pool counts, avoiding a rescan when serve supplied them."""
        status_started = time.perf_counter()
        event_hub = getattr(ctx, "event_hub", None) or getattr(
            ctx.runtime_controller, "event_hub", None
        )
        publish = getattr(event_hub, "publish", None)
        if not callable(publish):
            return
        pool_status: dict[str, object]
        if counts is not None:
            pool_status = {
                "pool_available_count": max(0, int(counts.get("available", 0))),
                "pool_raw_count": max(
                    0,
                    int(counts.get("raw", counts.get("available", 0))),
                ),
                "pool_pending_count": max(0, int(counts.get("pending", 0))),
                "pool_pending_eval_count": max(0, int(counts.get("pending_eval", 0))),
                "pool_evaluated_pending_count": max(
                    0,
                    int(counts.get("evaluated_pending", 0)),
                ),
            }
        else:
            isolated_reader = getattr(
                ctx.database,
                "count_pool_readiness_isolated_async",
                None,
            )
            if callable(isolated_reader):
                exact = await isolated_reader()
                pool_status = {
                    "pool_available_count": max(0, int(exact.get("available", 0))),
                    "pool_raw_count": max(0, int(exact.get("raw", 0))),
                    "pool_pending_count": max(0, int(exact.get("pending", 0))),
                    "pool_pending_eval_count": max(
                        0,
                        int(exact.get("pending_eval", 0)),
                    ),
                    "pool_evaluated_pending_count": max(
                        0,
                        int(exact.get("evaluated_pending", 0)),
                    ),
                }
            else:
                pool_status = await asyncio.to_thread(_runtime_pool_status_payload)
        controller_target = getattr(ctx.runtime_controller, "pool_target_count", None)
        if controller_target is not None:
            pool_status["pool_target_count"] = max(0, int(controller_target))
        event = {
            "type": "refresh.pool_updated",
            "phase": "done",
            "message": message,
            **pool_status,
        }
        with suppress(Exception):
            result = publish(event)
            if asyncio.iscoroutine(result):
                await result
        logger.info(
            "recommendation_status_publish status_publish_ms=%.1f exact=%s",
            (time.perf_counter() - status_started) * 1000.0,
            counts is None,
        )

    def _schedule_exact_pool_status_snapshot() -> None:
        """Refresh exact counts after the HTTP response-critical work."""
        task = asyncio.create_task(_publish_pool_status_snapshot())
        _fire_and_forget_tasks.add(task)
        task.add_done_callback(_fire_and_forget_tasks.discard)

    add_pool_commit_subscriber = getattr(ctx, "add_pool_inventory_commit_subscriber", None)
    if callable(add_pool_commit_subscriber):
        add_pool_commit_subscriber(_publish_pool_status_snapshot)
    set_pool_commit_callback = getattr(
        ctx.recommendation_engine,
        "set_pool_inventory_commit_callback",
        None,
    )
    if callable(set_pool_commit_callback):
        set_pool_commit_callback(ctx.pool_inventory_commit_callback)

    async def _run_auto_replenishment(trigger: Callable[[], Any]) -> None:
        try:
            await trigger()
        except Exception:
            logger.exception("Automatic pool replenishment failed")

    async def _request_runtime_replenishment(
        *,
        reason: str,
        force: bool = False,
    ) -> dict[str, object] | None:
        request = getattr(ctx.runtime_controller, "request_replenishment", None)
        if callable(request):
            with suppress(Exception):
                result = await request(reason=reason, force=force)
                if isinstance(result, dict):
                    return cast("dict[str, object]", result)
            return None
        if force:
            trigger = getattr(ctx.runtime_controller, "trigger_manual_refresh", None)
            if callable(trigger):
                with suppress(Exception):
                    result = await trigger()
                    if isinstance(result, dict):
                        return cast("dict[str, object]", result)
            return None

        legacy_name = {
            "event_ingest": "refresh_after_event_ingest",
            "feedback": "refresh_after_feedback",
            "init_completed": "refresh_after_init",
        }.get(reason)
        if legacy_name:
            legacy = getattr(ctx.runtime_controller, legacy_name, None)
            if callable(legacy):
                with suppress(Exception):
                    result = await legacy()
                    if isinstance(result, dict):
                        return cast("dict[str, object]", result)
        return None

    async def _trigger_replenishment_if_needed(
        *,
        force: bool = False,
        available_count: int | None = None,
    ) -> None:
        """Fire a background Discovery refresh when the pool runs low."""
        if not force:
            curator = getattr(ctx.recommendation_engine, "_curator", None)
            if curator is None or not hasattr(curator, "needs_replenishment"):
                return
            count_gate = getattr(curator, "needs_replenishment_for_count", None)
            if available_count is not None and callable(count_gate):
                needs_replenishment = bool(count_gate(available_count))
            else:
                needs_replenishment = await asyncio.to_thread(curator.needs_replenishment)
            if not needs_replenishment:
                return

        nonlocal auto_replenishment_started_at, auto_replenishment_task
        now = time.monotonic()
        if auto_replenishment_task is not None and not auto_replenishment_task.done():
            logger.debug("Pool low - automatic replenishment already running; skipping")
            return
        if now - auto_replenishment_started_at < _AUTO_REPLENISH_DEBOUNCE_SECONDS:
            logger.debug("Pool low - automatic replenishment recently requested; skipping")
            return

        auto_replenishment_started_at = now
        logger.info("Pool low - triggering automatic replenishment")
        reason = "pool_empty" if force else "pool_low_after_recommendation_refresh"
        task = asyncio.create_task(
            _run_auto_replenishment(
                lambda: _request_runtime_replenishment(reason=reason, force=True)
            )
        )
        auto_replenishment_task = task
        _fire_and_forget_tasks.add(task)
        task.add_done_callback(_fire_and_forget_tasks.discard)

    def _platform_scope_kwargs(source_platform: str) -> dict[str, str]:
        """Forward the platform scope only when the client actually sent one.

        Engines and test doubles that predate platform scoping implement the
        historical signature, so an unscoped request must keep its old call
        shape rather than passing an empty keyword they cannot accept.
        """
        scope = str(source_platform or "").strip()
        return {"source_platform": scope} if scope else {}

    def _scoped_batch_came_up_short(
        source_platform: str,
        items: list[Any],
        limit: int,
    ) -> bool:
        """Whether a platform-scoped request under-delivered.

        A short scoped batch is the only signal that one platform ran dry
        while the pool as a whole looks healthy, so it wakes the existing
        replenishment entry point. This only requests a background refresh —
        no discovery runs inside the HTTP request, and nothing here promises
        the platform is refilled by the time the response returns.
        """
        if not str(source_platform or "").strip():
            return False
        return len(items) < max(0, int(limit))

    @app.get(
        "/api/recommendations/platform-availability",
        response_model=PlatformAvailabilityResponse,
    )
    async def recommendation_platform_availability() -> PlatformAvailabilityResponse:
        """Report servable inventory per canonical platform for the tab badges."""
        loader = getattr(ctx.database, "load_pool_platform_availability_async", None)
        if not callable(loader):
            raise HTTPException(
                status_code=503,
                detail="platform availability is unavailable on this storage backend",
            )
        try:
            snapshot = await loader(xhs_self_nickname=_xhs_self_nickname())
        except Exception as exc:
            # Never answer a failed read with zeros: the desktop keeps its last
            # good snapshot, and a silent all-zero would read as "no stock
            # anywhere" and disable auto-load across every platform.
            logger.exception("Failed to read platform availability snapshot")
            raise HTTPException(
                status_code=500,
                detail=f"failed to read platform availability: {exc}",
            ) from exc
        by_platform = {
            str(name): max(0, int(count))
            for name, count in dict(getattr(snapshot, "by_platform", {}) or {}).items()
        }
        return PlatformAvailabilityResponse(
            total_available=max(0, int(getattr(snapshot, "total_available", 0))),
            by_platform=by_platform,
        )

    @app.post("/api/recommendations/reshuffle", response_model=RecommendationReshuffleResponse)
    async def reshuffle_recommendations(
        payload: Annotated[RecommendationReshuffleIn | None, Body()] = None,
    ) -> RecommendationReshuffleResponse:
        _invalidate_recommendation_snapshot()
        request_started = time.perf_counter()
        precheck_ms = 0.0
        if ctx.recommendation_engine is None or ctx.soul_engine is None:
            return RecommendationReshuffleResponse(items=[])
        result_fn = getattr(
            ctx.recommendation_engine,
            "reshuffle_recommendations_with_result",
            None,
        )
        if not callable(result_fn):
            precheck_started = time.perf_counter()
            if await asyncio.to_thread(_pool_available_count) == 0:
                await _trigger_replenishment_if_needed(force=True)
                return RecommendationReshuffleResponse(items=[])
            precheck_ms = (time.perf_counter() - precheck_started) * 1000.0
        profile_started = time.perf_counter()
        try:
            profile = await ctx.soul_engine.get_profile()
        except Exception:
            return RecommendationReshuffleResponse(items=[])
        profile_ms = (time.perf_counter() - profile_started) * 1000.0
        excluded_bvids = list(
            dict.fromkeys(
                bvid.strip()
                for bvid in (payload.excluded_bvids if payload is not None else [])
                if bvid and bvid.strip()
            )
        )
        source_platform = payload.source_platform if payload is not None else ""
        scope_kwargs = _platform_scope_kwargs(source_platform)
        if callable(result_fn):
            serve_result = await result_fn(
                profile=profile,
                excluded_bvids=excluded_bvids,
                limit=10,
                **scope_kwargs,
            )
            items = serve_result.items
            counts_after = dict(serve_result.pool_counts_after)
            timings = serve_result.timings
        else:
            items = await ctx.recommendation_engine.reshuffle_recommendations(
                profile=profile,
                excluded_bvids=excluded_bvids,
                limit=10,
                **scope_kwargs,
            )
            counts_after = {}
            timings = None
        items = _filter_recommendation_objects_for_latest_dislikes(items)
        if items:
            from openbiliclaw.sources.event_format import SOURCE_WEB, build_event

            returned_item_ids = [
                str(getattr(getattr(item, "content", None), "bvid", "") or "").strip()
                for item in items
            ]
            returned_item_ids = [item_id for item_id in returned_item_ids if item_id]
            reshuffle_event = build_event(
                event_type="reshuffle",
                source_platform=SOURCE_WEB,
                title="推荐列表",
                context="你在推荐页换了一批内容。",
                metadata={
                    "recommendation_source_platform": source_platform or "all",
                    "excluded_item_ids": excluded_bvids[:100],
                    "returned_item_ids": returned_item_ids[:100],
                    "batch_size": len(items),
                    "event_namespace": "recommendation",
                    "profile_update_owner": "generic",
                },
            )
            reshuffle_receipt = await event_ingress.accept(
                reshuffle_event,
                producer="web",
            )
            if reshuffle_receipt.accepted != 1:
                raise HTTPException(status_code=422, detail="reshuffle event rejected")
        _schedule_exact_pool_status_snapshot()
        available_after = counts_after.get("available")
        await _trigger_replenishment_if_needed(
            force=available_after == 0 or _scoped_batch_came_up_short(source_platform, items, 10),
            available_count=available_after,
        )
        logger.info(
            "recommendation_request_timing action=reshuffle precheck_ms=%.1f "
            "profile_ms=%.1f pool_snapshot_ms=%.1f embedding_ms=%.1f "
            "selector_worker_ms=%.1f event_loop_resume_delay_ms=%.1f "
            "persist_ms=%.1f status_publish_ms=0.0 total_ms=%.1f",
            precheck_ms,
            profile_ms,
            float(getattr(timings, "pool_snapshot_ms", 0.0)),
            float(getattr(timings, "embedding_ms", 0.0)),
            float(getattr(timings, "selector_worker_ms", 0.0)),
            float(getattr(timings, "event_loop_resume_delay_ms", 0.0)),
            float(getattr(timings, "persist_ms", 0.0)),
            (time.perf_counter() - request_started) * 1000.0,
        )
        return RecommendationReshuffleResponse(items=_serialize_recommendation_items(items))

    @app.post("/api/recommendations/append", response_model=RecommendationReshuffleResponse)
    async def append_recommendations(
        payload: RecommendationAppendIn,
    ) -> RecommendationReshuffleResponse:
        _invalidate_recommendation_snapshot()
        request_started = time.perf_counter()
        precheck_ms = 0.0
        if ctx.recommendation_engine is None or ctx.soul_engine is None:
            return RecommendationReshuffleResponse(items=[])
        result_fn = getattr(
            ctx.recommendation_engine,
            "append_recommendations_with_result",
            None,
        )
        if not callable(result_fn):
            precheck_started = time.perf_counter()
            if await asyncio.to_thread(_pool_available_count) == 0:
                await _trigger_replenishment_if_needed(force=True)
                return RecommendationReshuffleResponse(items=[])
            precheck_ms = (time.perf_counter() - precheck_started) * 1000.0
        profile_started = time.perf_counter()
        try:
            profile = await ctx.soul_engine.get_profile()
        except Exception:
            return RecommendationReshuffleResponse(items=[])
        profile_ms = (time.perf_counter() - profile_started) * 1000.0
        scope_kwargs = _platform_scope_kwargs(payload.source_platform)
        if callable(result_fn):
            serve_result = await result_fn(
                profile=profile,
                excluded_bvids=payload.excluded_bvids,
                limit=10,
                **scope_kwargs,
            )
            items = serve_result.items
            counts_after = dict(serve_result.pool_counts_after)
            timings = serve_result.timings
        else:
            items = await ctx.recommendation_engine.append_recommendations(
                profile=profile,
                excluded_bvids=payload.excluded_bvids,
                limit=10,
                **scope_kwargs,
            )
            counts_after = {}
            timings = None
        items = _filter_recommendation_objects_for_latest_dislikes(items)
        _schedule_exact_pool_status_snapshot()
        available_after = counts_after.get("available")
        await _trigger_replenishment_if_needed(
            force=(
                available_after == 0
                or _scoped_batch_came_up_short(payload.source_platform, items, 10)
            ),
            available_count=available_after,
        )
        logger.info(
            "recommendation_request_timing action=append precheck_ms=%.1f "
            "profile_ms=%.1f pool_snapshot_ms=%.1f embedding_ms=%.1f "
            "selector_worker_ms=%.1f event_loop_resume_delay_ms=%.1f "
            "persist_ms=%.1f status_publish_ms=0.0 total_ms=%.1f",
            precheck_ms,
            profile_ms,
            float(getattr(timings, "pool_snapshot_ms", 0.0)),
            float(getattr(timings, "embedding_ms", 0.0)),
            float(getattr(timings, "selector_worker_ms", 0.0)),
            float(getattr(timings, "event_loop_resume_delay_ms", 0.0)),
            float(getattr(timings, "persist_ms", 0.0)),
            (time.perf_counter() - request_started) * 1000.0,
        )
        return RecommendationReshuffleResponse(items=_serialize_recommendation_items(items))

    @app.post("/api/recommendations/refresh", response_model=RecommendationRefreshResponse)
    async def refresh_recommendations() -> RecommendationRefreshResponse:
        result = await _request_runtime_replenishment(reason="manual", force=True)
        if not isinstance(result, dict):
            return RecommendationRefreshResponse(
                ok=True,
                accepted=False,
                state="idle",
                reason="runtime_unavailable",
            )
        return RecommendationRefreshResponse(
            ok=True,
            accepted=bool(result.get("accepted", False)),
            state=str(result.get("state", "idle")),
            reason=str(result.get("reason", "")),
        )

    @app.get("/api/runtime-status", response_model=RuntimeStatusResponse)
    async def runtime_status() -> RuntimeStatusResponse:
        get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
        if callable(get_runtime_status):
            payload = dict(await asyncio.to_thread(get_runtime_status))
        else:
            # Scheduler health remains observable even in a degraded runtime;
            # returning here used to hide durable work exactly when operators
            # needed recovery diagnostics most.
            payload = {
                "initialized": False,
                "recommendation_count": 0,
                "pending_signal_events": 0,
                "unread_count": 0,
            }
        get_account_sync_status = getattr(ctx.account_sync_service, "get_runtime_status", None)
        if callable(get_account_sync_status):
            payload.update(get_account_sync_status())
        get_update_status = getattr(ctx.auto_update_service, "get_runtime_status", None)
        if callable(get_update_status):
            payload.update(get_update_status())
        payload.update(feedback_batch_scheduler.status_payload())
        payload.update(chat_reply_scheduler.status_payload())
        payload.update(image_fetch_coordinator.status_payload())
        settlement_queue = getattr(ctx, "dialogue_settlement_queue", None)
        if settlement_queue is not None:
            payload["dialogue_settlement_depth"] = int(
                getattr(settlement_queue, "depth", 0) or 0
            )
            payload["dialogue_settlement_max_depth"] = int(
                getattr(settlement_queue, "max_depth", 0) or 0
            )
            payload["dialogue_settlement_dropped"] = int(
                getattr(settlement_queue, "dropped_jobs", 0) or 0
            )
        recommendation_engine = getattr(ctx, "recommendation_engine", None)
        outbox_depth = getattr(recommendation_engine, "serve_outbox_depth", None)
        if callable(outbox_depth):
            payload["worker_outbox_depth"] = int(outbox_depth() or 0)
        from openbiliclaw.runtime.worker_status import WorkerStatusStore

        worker_status_store = WorkerStatusStore(
            active_runtime_data_path / "runtime" / "worker_status.json"
        )
        payload.update(worker_status_store.status_payload())
        return RuntimeStatusResponse(**payload)

    @app.post("/api/agent-bridge")
    async def agent_bridge(payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one OpenClaw agent-bridge command against a warm adapter.

        Mirrors the ``openbiliclaw.integrations.openclaw.cli`` JSON contract
        (``{ok, data}`` / ``{ok: false, error, error_type}``) but runs inside
        the warm serve-api process, so agent hosts avoid the per-call Python
        import cold start.  The adapter is built lazily on first use and cached
        on ``app.state``.
        """
        command = str(payload.get("command") or "").strip()
        argv = payload.get("argv")
        if not command:
            return {"ok": False, "error": "missing command", "error_type": "validation_error"}
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            return {
                "ok": False,
                "error": "argv must be an array of strings",
                "error_type": "validation_error",
            }

        adapter = getattr(app.state, "agent_bridge_adapter", None)
        if adapter is None:
            lock = getattr(app.state, "agent_bridge_adapter_lock", None)
            if lock is None:
                lock = asyncio.Lock()
                app.state.agent_bridge_adapter_lock = lock
            async with lock:
                adapter = getattr(app.state, "agent_bridge_adapter", None)
                if adapter is None:
                    from openbiliclaw.integrations.openclaw.bootstrap import (
                        build_openclaw_adapter,
                    )

                    adapter = await asyncio.to_thread(build_openclaw_adapter)
                    app.state.agent_bridge_adapter = adapter

        from openbiliclaw.integrations.openclaw.cli import _build_parser, _run_command

        parser = _build_parser()
        try:
            args = parser.parse_args([command, *argv])
        except SystemExit:
            return {
                "ok": False,
                "error": f"invalid command or arguments: {command!r}",
                "error_type": "validation_error",
            }
        try:
            return await _run_command(args, adapter)
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            return {"ok": False, "error": str(exc), "error_type": "operation_error"}

    def _backend_update_status() -> BackendUpdateStatusOut:
        get_update_status = getattr(ctx.auto_update_service, "get_update_status", None)
        if callable(get_update_status):
            status = get_update_status()
            return BackendUpdateStatusOut.model_validate(
                dict(status) if isinstance(status, dict) else {}
            )
        get_runtime_update_status = getattr(ctx.auto_update_service, "get_runtime_status", None)
        if callable(get_runtime_update_status):
            runtime_status = dict(get_runtime_update_status())
            return BackendUpdateStatusOut(
                state=str(runtime_status.get("backend_update_state", "unknown")),
                auto_update_enabled=bool(runtime_status.get("auto_update_enabled", False)),
                install_mode=str(runtime_status.get("install_mode", "")),
                current_version=str(runtime_status.get("current_version", "")),
                latest_version=str(runtime_status.get("latest_remote_version", "")),
                latest_tag=str(runtime_status.get("latest_remote_version", "")),
                last_check_at=str(runtime_status.get("last_update_check_at", "")),
                last_error=str(runtime_status.get("last_update_error", "")),
                reason=str(runtime_status.get("backend_update_reason", "none")),
            )
        return BackendUpdateStatusOut(
            state="disabled",
            auto_update_enabled=False,
            current_version="",
            latest_version="",
            latest_tag="",
            last_check_at="",
            last_error="",
            reason="none",
        )

    @app.get("/api/update-status", response_model=UpdateStatusResponse)
    async def update_status() -> UpdateStatusResponse:
        return UpdateStatusResponse(backend=_backend_update_status())

    @app.post("/api/update/check", response_model=UpdateStatusResponse)
    async def update_check(_payload: UpdateCheckIn | None = None) -> UpdateStatusResponse:
        check_now = getattr(ctx.auto_update_service, "check_now", None)
        if callable(check_now):
            backend = await check_now()
        else:
            backend = _backend_update_status()
        return UpdateStatusResponse(backend=BackendUpdateStatusOut.model_validate(backend))

    @app.post("/api/update/apply")
    async def update_apply(payload: UpdateApplyIn) -> JSONResponse:
        request_apply = getattr(ctx.auto_update_service, "request_apply", None)
        if not callable(request_apply):
            return JSONResponse(
                status_code=409,
                content={
                    "target": "backend",
                    "state": "unsupported",
                    "reason": "unsupported_install_mode",
                    "accepted": False,
                    "observe_via": "runtime-stream",
                },
            )
        status_code, body = await request_apply(tag=payload.tag)
        return JSONResponse(status_code=int(status_code), content=body)

    @app.get("/api/diagnostics/alerts")
    async def diagnostics_alerts(since_id: int = 0, limit: int = 100) -> dict[str, Any]:
        """Recent LLM / embedding anomaly alerts for the 异常报警 feed.

        Backs the Web console and browser-extension settings「日志」tab.
        Pass ``since_id`` to fetch only alerts newer than a previously seen
        row; ``limit`` caps the page size (server max 500).
        """
        return get_diagnostics_alert_buffer().snapshot(since_id=since_id, limit=limit)

    @app.get("/api/notifications/pending", response_model=PendingNotificationResponse)
    async def pending_notification() -> PendingNotificationResponse:
        get_pending_notification = getattr(ctx.runtime_controller, "get_pending_notification", None)
        item = get_pending_notification() if callable(get_pending_notification) else None
        if item is None:
            get_notification_candidate = getattr(ctx.database, "get_notification_candidate", None)
            if callable(get_notification_candidate):
                candidate = get_notification_candidate(min_confidence=0.82)
                if candidate is not None:
                    from openbiliclaw.recommendation.exclusion import (
                        filter_recommendation_rows,
                    )

                    disliked_topics, _digest = _effective_recommendation_dislikes()
                    if filter_recommendation_rows(
                        [candidate],
                        disliked_topics,
                        restore_on_total_fuzzy_match=False,
                    ):
                        item = {
                            "recommendation_id": int(candidate["id"]),
                            "bvid": str(candidate.get("bvid", "")),
                            "title": str(candidate.get("title", "")),
                            "reason": str(candidate.get("expression", "")),
                        }
        if item is not None:
            from openbiliclaw.recommendation.exclusion import filter_recommendation_rows

            disliked_topics, _digest = _effective_recommendation_dislikes()
            if not filter_recommendation_rows(
                [item],
                disliked_topics,
                restore_on_total_fuzzy_match=False,
            ):
                item = None
        if item is None:
            return PendingNotificationResponse(item=None)
        return PendingNotificationResponse(item=PendingNotificationOut(**item))

    @app.get(
        "/api/cognition-updates/pending",
        response_model=PendingCognitionUpdateResponse,
    )
    async def pending_cognition_update() -> PendingCognitionUpdateResponse:
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        if not callable(load_cognition_updates):
            return PendingCognitionUpdateResponse(item=None)
        updates = [
            item
            for item in load_cognition_updates()
            if isinstance(item, dict) and not bool(item.get("notified", False))
        ]
        if not updates:
            return PendingCognitionUpdateResponse(item=None)
        latest = updates[-1]
        return PendingCognitionUpdateResponse(
            item=PendingCognitionUpdateOut(
                id=str(latest.get("id", "")),
                kind=str(latest.get("kind", "")),
                summary=str(latest.get("summary", "")),
            )
        )

    @app.post(
        "/api/cognition-updates/seen",
        response_model=CognitionUpdateSeenResponse,
    )
    async def cognition_update_seen(
        payload: CognitionUpdateSeenIn,
    ) -> CognitionUpdateSeenResponse:
        update_id = payload.id.strip()
        if not update_id:
            raise HTTPException(status_code=422, detail="Cognition update id is required.")
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        save_cognition_updates = getattr(ctx.memory_manager, "save_cognition_updates", None)
        if not callable(load_cognition_updates) or not callable(save_cognition_updates):
            raise HTTPException(status_code=500, detail="Cognition update storage unavailable.")
        updates = load_cognition_updates()
        found = False
        for item in updates:
            if not isinstance(item, dict):
                continue
            if str(item.get("id", "")).strip() != update_id:
                continue
            item["notified"] = True
            found = True
            break
        if not found:
            raise HTTPException(status_code=404, detail="Cognition update not found.")
        save_cognition_updates(updates)
        return CognitionUpdateSeenResponse(ok=True, id=update_id)

    @app.post("/api/delight/trigger")
    async def trigger_delight(payload: dict[str, Any] | None = None) -> Any:
        """Manually push N distinct delight candidates via WebSocket.

        Body: ``{"count": 3}``. For testing the queue UI: pulls the top N
        un-notified candidates from the pool and publishes a
        ``delight.candidate`` event for each one in succession, **without**
        marking any as notified. That way you can re-trigger the same
        batch repeatedly while iterating on the popup-side queue, and
        the popup's own ``/api/delight/pending`` calls still see them
        afterwards.

        Cooldown is cleared at the end so the proactive-push loop
        isn't gated.
        """
        count = 1
        if isinstance(payload, dict):
            try:
                count = max(1, min(20, int(payload.get("count", 1))))
            except (ValueError, TypeError):
                count = 1

        from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD

        threshold = DEFAULT_DELIGHT_THRESHOLD
        dynamic_threshold = getattr(ctx.runtime_controller, "_dynamic_delight_threshold", None)
        if callable(dynamic_threshold):
            with suppress(Exception):
                threshold = float(dynamic_threshold())

        candidates = ctx.database.get_delight_candidates(
            min_delight_score=threshold,
            limit=count,
        )
        pushed: list[str] = []
        for row in candidates:
            payload_event = {
                "type": "delight.candidate",
                "phase": "ready",
                "message": "发现了一条你可能会意外喜欢的内容",
                "bvid": str(row.get("bvid", "")),
                "item_key": str(row.get("item_key", "")),
                "content_id": str(row.get("content_id", "") or row.get("bvid", "")),
                "title": str(row.get("title", "")),
                "delight_reason": str(row.get("delight_reason", "")),
                "delight_score": float(row.get("delight_score", 0.0) or 0.0),
                "delight_hook": str(row.get("delight_hook", "")),
                "cover_url": str(row.get("cover_url", "")),
                "content_url": str(row.get("content_url", "")),
                "source_platform": str(row.get("source_platform", "bilibili")),
                "published_at": str(row.get("published_at", "") or ""),
                "published_label": str(row.get("published_label", "") or ""),
                # body_text / content_type let the desktop delight card derive a
                # readable title for legacy rows still holding answer_<id> (#79).
                "content_type": str(row.get("content_type", "") or ""),
                "body_text": str(row.get("body_text", "") or ""),
                "view_count": int(row.get("view_count", 0) or 0),
                "like_count": int(row.get("like_count", 0) or 0),
                "comment_count": int(row.get("comment_count", 0) or 0),
                "share_count": int(row.get("share_count", 0) or 0),
                "danmaku_count": int(row.get("danmaku_count", 0) or 0),
                "favorite_count": int(
                    row.get("favorite_count", 0) or row.get("collect_count", 0) or 0
                ),
            }
            with suppress(Exception):
                await ctx.event_hub.publish(payload_event)
            pushed.append(str(payload_event["bvid"]))

        # Clear cooldown so the regular push loop isn't gated after manual
        # trigger.
        memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        if memory_manager is not None:
            update_state = getattr(memory_manager, "update_discovery_runtime_state", None)
            if callable(update_state):
                update_state(lambda state: state.pop("last_delight_notification_at", None))
            else:
                state = memory_manager.load_discovery_runtime_state()
                state.pop("last_delight_notification_at", None)
                memory_manager.save_discovery_runtime_state(state)
        return {"ok": True, "pushed_count": len(pushed), "bvids": pushed}

    @app.get("/api/delight/pending", response_model=PendingDelightResponse)
    async def pending_delight() -> PendingDelightResponse:
        get_pending_delight = getattr(ctx.runtime_controller, "get_pending_delight", None)
        item = get_pending_delight() if callable(get_pending_delight) else None
        if item is None:
            return PendingDelightResponse(item=None)
        return PendingDelightResponse(item=PendingDelightOut(**item))

    @app.get("/api/delight/pending-batch")
    async def pending_delight_batch(limit: int | None = None) -> dict[str, Any]:
        """Return un-notified delight candidates.

        When ``limit`` is omitted the shared
        ``scheduler.delight_queue_limit`` setting decides the queue size.
        Unlike ``/api/delight/pending`` this ignores the 4-hour
        notification cooldown — it's intended for the popup to
        re-hydrate the full queue on init, not for active push gating.
        Honors ``disliked_topics`` substring filter same as the singular
        endpoint.

        ``include_liked=True``: a liked delight keeps its queue slot across
        re-hydration (popup reopen / delight.refreshed) instead of silently
        vanishing — positive feedback keeps the card visible until the user
        dismisses it. Such rows come back with ``state="liked"`` so clients
        render the already-liked treatment.
        """
        from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD

        configured_limit = getattr(
            getattr(getattr(ctx, "config", None), "scheduler", None),
            "delight_queue_limit",
            20,
        )
        requested_limit = configured_limit if limit is None else limit
        threshold = DEFAULT_DELIGHT_THRESHOLD
        dynamic_threshold = getattr(ctx.runtime_controller, "_dynamic_delight_threshold", None)
        if callable(dynamic_threshold):
            with suppress(Exception):
                threshold = float(dynamic_threshold())
        rows = ctx.database.get_delight_candidates(
            min_delight_score=threshold,
            limit=max(1, min(100, int(requested_limit))),
            include_liked=True,
            include_delivered=True,
        )
        # Reuse the same disliked-topic filter as get_pending_delight by
        # going through the runtime controller's loader if possible.
        controller = ctx.runtime_controller
        load_phrases = getattr(controller, "_load_disliked_topic_phrases", None)
        disliked_phrases = load_phrases() if callable(load_phrases) else []

        def passes_filter(row: dict[str, Any]) -> bool:
            haystack = f"{str(row.get('title', '')).lower()} {str(row.get('tags', '')).lower()}"
            return not any(p and p in haystack for p in disliked_phrases)

        items = [
            {
                "bvid": str(row.get("bvid", "")),
                "item_key": str(row.get("item_key", "")),
                "content_id": str(row.get("content_id", "") or row.get("bvid", "")),
                "title": str(row.get("title", "")),
                "delight_reason": str(row.get("delight_reason", "")),
                "delight_score": float(row.get("delight_score", 0.0) or 0.0),
                "delight_hook": str(row.get("delight_hook", "")),
                "cover_url": str(row.get("cover_url", "")),
                "content_url": str(row.get("content_url", "")),
                "source_platform": str(row.get("source_platform", "bilibili")),
                "published_at": str(row.get("published_at", "") or ""),
                "published_label": str(row.get("published_label", "") or ""),
                # body_text / content_type let the desktop delight card derive a
                # readable title for legacy rows still holding answer_<id> (#79).
                "content_type": str(row.get("content_type", "") or ""),
                "body_text": str(row.get("body_text", "") or ""),
                # Engagement stats so the delight card shows the same ▶/👍/💬/🔁 row
                # as the grid (0 = not fetched → the card renders nothing for it).
                "view_count": int(row.get("view_count", 0) or 0),
                "like_count": int(row.get("like_count", 0) or 0),
                "comment_count": int(row.get("comment_count", 0) or 0),
                "share_count": int(row.get("share_count", 0) or 0),
                "danmaku_count": int(row.get("danmaku_count", 0) or 0),
                "favorite_count": int(
                    row.get("favorite_count", 0) or row.get("collect_count", 0) or 0
                ),
                "state": (
                    "liked" if str(row.get("feedback_type", "") or "") == "like" else "pending"
                ),
            }
            for row in rows
            if passes_filter(row)
        ]
        return {"items": items}

    @app.post("/api/delight/sent", response_model=DelightAckResponse)
    async def mark_delight_sent(payload: DelightAckIn) -> DelightAckResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="Delight bvid is required.")
        mark_sent = getattr(ctx.runtime_controller, "mark_delight_sent", None)
        if callable(mark_sent):
            mark_sent(bvid)
        else:
            ctx.database.mark_delight_notified(bvid)
        return DelightAckResponse(ok=True, bvid=bvid)

    @app.post("/api/delight/respond")
    async def respond_to_delight(payload: DelightResponseIn) -> Any:
        """User responds to a delight (surprise) recommendation.

        Body:
        ``{ "bvid": "...", "title": "...", "response": "view"|"like"|"dislike"|"chat",
        "message": "..." }``. ``like`` / ``chat`` update learning signals and keep
        the delight in the queue; ``view`` keeps the card visible in-session but
        marks the candidate read (same semantics as the recommendation pool's
        ``shown`` flag — a browsed surprise doesn't reappear on the next queue
        re-hydration); ``dismiss`` records the canonical identity as already
        handled so it is permanently excluded from both recommendation channels;
        ``dislike`` consumes the candidate and records a negative preference.
        """
        from fastapi.responses import JSONResponse

        bvid = payload.bvid.strip()
        title = payload.title.strip()
        response_type = payload.response.strip().lower()
        if not bvid:
            raise HTTPException(status_code=422, detail="bvid is required")
        if response_type not in {"view", "like", "dislike", "chat", "dismiss"}:
            raise HTTPException(
                status_code=422,
                detail="response must be view, like, dislike, chat, or dismiss",
            )

        reaction_receipt: Any = None
        if response_type in {"like", "dislike", "dismiss"}:
            from openbiliclaw.sources.event_format import SOURCE_WEB, build_event

            reaction_event = build_event(
                event_type="feedback",
                source_platform=SOURCE_WEB,
                title=title or bvid,
                metadata={
                    "bvid": bvid,
                    "content_id": bvid,
                    "feedback_type": response_type,
                    "event_namespace": "recommendation",
                    "source": "delight_response",
                    "profile_update_owner": "content_feedback",
                },
            )
            reaction_event["ingest_key"] = payload.request_id.strip()
            receipt = await event_ingress.accept(
                reaction_event,
                producer="delight",
            )
            if receipt.accepted != 1 or receipt.rejected:
                raise HTTPException(status_code=422, detail="delight reaction was rejected")
            reaction_receipt = receipt.items[0]
            stored_reaction = _durable_ingress_row(
                ctx,
                event_id=reaction_receipt.event_id,
                inserted=reaction_receipt.inserted,
                submitted_event=reaction_event,
            )
            stored_metadata = _event_row_metadata(stored_reaction)
            if (
                str(stored_metadata.get("bvid") or "").strip() != bvid
                or str(stored_metadata.get("feedback_type") or "").strip().lower() != response_type
            ):
                raise HTTPException(
                    status_code=409,
                    detail="request_id was already used for a different delight reaction",
                )

        def reaction_response(action: str) -> JSONResponse:
            content: dict[str, object] = {"ok": True, "action": action, "bvid": bvid}
            if reaction_receipt is not None:
                content.update(
                    {
                        "event_id": reaction_receipt.event_id,
                        "duplicate": reaction_receipt.duplicate,
                        "processing": "queued",
                    }
                )
            return JSONResponse(content=content)

        def mark_delight_consumed() -> None:
            mark_sent = getattr(ctx.runtime_controller, "mark_delight_sent", None)
            if callable(mark_sent):
                mark_sent(bvid)
            else:
                ctx.database.mark_delight_notified(bvid)

        def mark_delight_seen() -> None:
            mark_seen = getattr(ctx.runtime_controller, "mark_delight_seen", None)
            if callable(mark_seen):
                mark_seen(bvid)
                return
            mark_seen = getattr(ctx.database, "mark_delight_seen", None)
            if callable(mark_seen):
                mark_seen(bvid)
                return
            mark_delight_consumed()

        if response_type == "view":
            # Browsing the content marks the candidate read — mirrors the
            # recommendation pool, where a served item flips to 'shown' and
            # is never re-served. The card keeps its in-session "viewed"
            # treatment; it just stops re-hydrating on the next queue load.
            # Direct DB mark (not mark_delight_consumed): viewing must not
            # bump the 4h proactive-push cooldown — engaging with one
            # surprise shouldn't delay discovery of the next.
            try:
                mark_viewed = getattr(ctx.database, "mark_delight_viewed", None)
                if callable(mark_viewed):
                    mark_viewed(bvid)
                else:
                    # Backward-compatible fallback for fakes/adapters that
                    # only expose the older delivered-only marker.
                    ctx.database.mark_delight_notified(bvid)
            except Exception:
                logger.debug("Failed to mark viewed delight bvid %s", bvid)
            return JSONResponse(content={"ok": True, "action": "viewed", "bvid": bvid})

        if response_type == "dismiss":
            try:
                mark_delight_seen()
            except Exception:
                logger.exception("Failed to permanently dismiss delight bvid %s", bvid)
                return JSONResponse(
                    status_code=500,
                    content={
                        "ok": False,
                        "action": "dismiss",
                        "bvid": bvid,
                        "error": "persist_failed",
                    },
                )
            return reaction_response("dismissed")

        if response_type == "like":
            # User marks this delight as liked WITHOUT having opened the
            # video. Treat as a strong positive feedback signal: boost
            # the row's relevance score and record a cognition update so
            # downstream scoring + UI both reflect the preference.
            ctx.database._execute_write(
                "UPDATE content_cache SET feedback_type='like', "
                "feedback_at=CURRENT_TIMESTAMP, "
                "relevance_score=MAX(COALESCE(relevance_score, 0.5), 0.65) "
                "WHERE bvid = ?",
                (bvid,),
            )
            label = title or bvid
            if reaction_receipt.inserted:
                _record_probe_cognition(
                    f"你喜欢惊喜推荐「{label}」，会多挖类似的。",
                    bvid,
                    "delight_like",
                )
                await _publish_probe_event(
                    "delight.liked",
                    f"好，「{label}」这类多来点。",
                    bvid,
                )
                _record_exploration_buffer_event(
                    domain=label,
                    source_event="card_more_like",
                    evidence_id=bvid,
                )
            return reaction_response("liked")

        if response_type == "dislike":
            ctx.database._execute_write(
                "UPDATE content_cache SET pool_status = 'purged_by_dislike', "
                "feedback_type='dislike', feedback_at=CURRENT_TIMESTAMP "
                "WHERE bvid = ?",
                (bvid,),
            )
            mark_delight_consumed()
            label = title or bvid
            if reaction_receipt.inserted:
                _record_probe_cognition(
                    f"你对惊喜推荐「{label}」不感兴趣。",
                    bvid,
                    "delight_dislike",
                )
                await _publish_probe_event(
                    "delight.disliked",
                    f"好，「{label}」这类先不推了。",
                    bvid,
                )
                _record_exploration_buffer_event(
                    domain=label,
                    source_event="negative",
                    evidence_id=bvid,
                )
            return reaction_response("disliked")

        # Chat
        raw_message = payload.message.strip()
        if not raw_message:
            raw_message = f"聊聊你为什么觉得「{title or bvid}」我会喜欢"
        contextual_message = f"[关于惊喜推荐「{title or bvid}」的反馈] {raw_message}"

        async def _run_delight_chat(dialogue_owner: Any) -> str:
            reply = await asyncio.wait_for(
                dialogue_owner.respond(contextual_message),
                timeout=30,
            )
            label = title or bvid
            _record_probe_cognition(
                f"关于惊喜推荐「{label}」你说：{raw_message}",
                bvid,
                "delight_chat",
                detail=f"你的反馈：{raw_message}\n阿b的回复：{reply}",
            )
            await _publish_probe_event(
                "delight.chat",
                f"关于「{label}」你说：{raw_message}",
                bvid,
            )
            return str(reply)

        try:
            reply = await _run_with_dialogue_execution(_run_delight_chat)
        except Exception as exc:
            logger.exception("Dialogue failed for delight chat: %s", bvid)
            return JSONResponse(
                content={
                    "ok": False,
                    "action": "chat",
                    "bvid": bvid,
                    "reply": safe_llm_failure_message(exc),
                }
            )
        return JSONResponse(content={"ok": True, "action": "chat", "bvid": bvid, "reply": reply})

    @app.post("/api/notifications/sent", response_model=NotificationAckResponse)
    async def mark_notification_sent(payload: NotificationAckIn) -> NotificationAckResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="Notification bvid is required.")
        mark_sent = getattr(ctx.runtime_controller, "mark_notification_sent", None)
        if callable(mark_sent):
            mark_sent(bvid)
        else:
            ctx.database.mark_notification_sent(bvid)
        return NotificationAckResponse(ok=True, bvid=bvid)

    @app.post("/api/chat")
    async def chat(payload: ChatIn) -> Any:
        from fastapi.responses import JSONResponse

        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Chat message is required.")

        async def _run_legacy_chat(dialogue_owner: Any) -> str:
            return str(
                await asyncio.wait_for(
                    dialogue_owner.respond(message),
                    timeout=120,
                )
            )

        try:
            # Bumped from 30s to 120s — deepseek with reasoning_effort=max
            # routinely takes 60-90s for one dialogue turn, so a 30s budget
            # truncated essentially every reply. Extension's AbortController
            # is sized to be generous enough to cover this end-to-end.
            reply = await _run_with_dialogue_execution(_run_legacy_chat)
        except Exception as exc:
            logger.exception("Chat dialogue failed")
            reply = safe_llm_failure_message(exc)
        return JSONResponse(content={"reply": reply})

    def _record_probe_cognition(
        summary: str,
        domain: str,
        action: str,
        *,
        source: str = "interest_probe",
        detail: str = "",
    ) -> None:
        """Write a cognition update so probe feedback shows in '阿b最近记住了什么'."""
        from datetime import datetime

        try:
            updates = ctx.memory_manager.load_cognition_updates()
            updates.append(
                {
                    "summary": summary,
                    "detail": detail or f"兴趣探针反馈：{action} — {domain}",
                    "created_at": datetime.now().isoformat(),
                    "source": source,
                    "tone": "success" if action == "confirmed" else "info",
                }
            )
            ctx.memory_manager.save_cognition_updates(updates)
        except Exception:
            logger.exception("Failed to record probe cognition update")

    async def _publish_probe_event(event_type: str, message: str, domain: str) -> None:
        """Push a probe result event via WebSocket."""
        event_hub = getattr(ctx.runtime_controller, "event_hub", None)
        publish = getattr(event_hub, "publish", None)
        if callable(publish):
            await publish(
                {
                    "type": event_type,
                    "phase": "ready",
                    "message": message,
                    "domain": domain,
                }
            )

    def _probe_metadata_from_active_item(
        get_active: Any,
        domain: str,
        *,
        include_category: bool = False,
        include_source_mode: bool = False,
    ) -> dict[str, object]:
        """Read active probe metadata before confirm/reject mutates state."""
        from openbiliclaw.soul.speculator import build_probe_axis

        if not callable(get_active):
            return {"domain": domain}
        try:
            active_items = list(get_active())
        except Exception:
            logger.debug("Failed to read active probe metadata", exc_info=True)
            return {"domain": domain}

        for item in active_items:
            spec_domain = str(getattr(item, "domain", "")).strip()
            if spec_domain.lower() != domain.lower():
                continue
            specifics = [
                str(getattr(specific, "name", "")).strip()
                for specific in getattr(item, "specifics", [])
                if str(getattr(specific, "name", "")).strip()
            ]
            axis = build_probe_axis(
                experience_mode=getattr(item, "experience_mode", ""),
                entry_load=getattr(item, "entry_load", ""),
            )
            metadata: dict[str, object] = {
                "domain": spec_domain or domain,
                "reason": str(getattr(item, "reason", "")).strip(),
            }
            if include_category:
                metadata["category"] = str(getattr(item, "category", "")).strip()
            if include_source_mode:
                source_mode = str(getattr(item, "source_mode", "")).strip()
                source_signal = str(getattr(item, "source_signal", "")).strip()
                if source_mode:
                    metadata["source_mode"] = source_mode
                if source_signal:
                    metadata["source_signal"] = source_signal
            if axis:
                metadata["axis"] = axis
            if specifics:
                metadata["specifics"] = specifics
            return metadata
        return {"domain": domain}

    def _probe_metadata_from_active_speculation(
        speculator: Any,
        domain: str,
    ) -> dict[str, object]:
        """Read active interest probe metadata before state mutation."""
        return _probe_metadata_from_active_item(
            getattr(speculator, "get_active_speculations", None),
            domain,
            include_category=True,
        )

    def _probe_metadata_from_active_avoidance(
        speculator: Any,
        domain: str,
    ) -> dict[str, object]:
        """Read active avoidance probe metadata before state mutation."""
        return _probe_metadata_from_active_item(
            getattr(speculator, "get_active_avoidances", None),
            domain,
            include_source_mode=True,
        )

    def _record_probe_feedback_history(
        domain: str,
        response: str,
        *,
        speculator: Any,
        message: str = "",
        classification: str = "",
        classifier: str = "",
        resulting_action: str = "",
        state_key: str = "probe_feedback_history",
        metadata_fn: Any | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Persist explicit user feedback for future probe novelty checks."""
        from openbiliclaw.soul.speculator import append_probe_feedback_history

        memory_manager = getattr(ctx, "memory_manager", None)
        if memory_manager is None:
            memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        load_state = getattr(memory_manager, "load_discovery_runtime_state", None)
        save_state = getattr(memory_manager, "save_discovery_runtime_state", None)
        update_state = getattr(memory_manager, "update_discovery_runtime_state", None)
        if not callable(update_state) and (not callable(load_state) or not callable(save_state)):
            return
        try:
            if metadata is not None:
                entry = dict(metadata)
            elif metadata_fn is not None:
                entry = metadata_fn(domain)
            else:
                entry = _probe_metadata_from_active_speculation(speculator, domain)
            entry["response"] = response
            if message:
                entry["message"] = message
                entry["raw_text_excerpt"] = message[:240]
            if classification:
                entry["classification"] = classification
            if classifier:
                entry["classifier"] = classifier
            if resulting_action:
                entry["resulting_action"] = resulting_action

            def _mutate(state: dict[str, object]) -> None:
                state[state_key] = append_probe_feedback_history(
                    state.get(state_key, []),
                    entry,
                )

            if callable(update_state):
                update_state(_mutate)
            else:
                load_state_fn = cast("Callable[[], dict[str, object]]", load_state)
                save_state_fn = cast("Callable[[dict[str, object]], None]", save_state)
                state = load_state_fn()
                _mutate(state)
                save_state_fn(state)
        except Exception:
            logger.exception("Failed to record probe feedback history")

    async def _judge_probe_sentiment(
        user_message: str,
        ai_reply: str,
        domain: str,
    ) -> str:
        """Judge the user's probe chat as a 4-way confirmation signal."""
        sentiment, _classifier = await _classify_probe_sentiment(
            user_message,
            ai_reply,
            domain,
        )
        return sentiment

    async def _classify_probe_sentiment(
        user_message: str,
        ai_reply: str,
        domain: str,
    ) -> tuple[str, str]:
        """Return ``(classification, classifier)`` for probe chat feedback."""
        llm_result = await _llm_judge_sentiment(user_message, ai_reply, domain)
        if llm_result in {"strong_positive", "weak_positive", "negative", "neutral_deferred"}:
            return llm_result, "llm"
        keyword_result = _keyword_judge_sentiment(user_message)
        if keyword_result != "neutral":
            return keyword_result, "keyword"
        return "neutral", "neutral_default"

    def _keyword_judge_sentiment(user_message: str) -> str:
        """Fallback keyword-based sentiment detection."""
        msg = user_message.lower()
        negative_terms = {
            "不喜欢",
            "不感兴趣",
            "不是这个意思",
            "别推",
            "没兴趣",
            "不想看",
        }
        # Explicit "shelve it for now" — routes to the defer state machine.
        # Ambiguous phrases (「不确定」「再看看」) are intentionally excluded:
        # they stay plain neutral (no state change). Checked AFTER negatives so
        # a message with both (e.g. 「不喜欢，先放着吧」) classifies negative.
        deferred_terms = {
            "暂时忽略",
            "先放着",
            "稍后再看",
            "以后再说",
            "回头再看",
            "过段时间再说",
        }
        strong_positive_terms = {
            "以后多推",
            "这就是我想看的",
            "我就喜欢",
            "加入我的画像",
        }
        weak_positive_terms = {
            "有点意思",
            "可以看看",
            "偶尔看看",
            "还行",
            "先试试",
        }
        if any(kw in msg for kw in negative_terms):
            return "negative"
        if any(kw in msg for kw in deferred_terms):
            return "neutral_deferred"
        if any(kw in msg for kw in strong_positive_terms):
            return "strong_positive"
        if any(kw in msg for kw in weak_positive_terms):
            return "weak_positive"
        return "neutral"

    async def _llm_judge_sentiment(
        user_message: str,
        ai_reply: str,
        domain: str,
    ) -> str:
        """LLM-based sentiment judgment for probe chat."""
        if ctx.recommendation_engine is None:
            return "neutral"
        llm = getattr(ctx.recommendation_engine, "_llm", None)
        if llm is None:
            return "neutral"
        from openbiliclaw.llm.prompts import build_probe_sentiment_prompt

        messages = build_probe_sentiment_prompt(domain=domain, user_message=user_message)
        try:
            # Intentionally carries core memory (chat-adjacent): classifying the
            # sentiment of a probe reply benefits from knowing who the user is, so
            # tone/intent is read in the user's own context. Kept per Task 8 audit.
            response = await asyncio.wait_for(
                llm.complete_with_core_memory(
                    system_instruction=messages[0]["content"],
                    user_input=messages[1]["content"],
                    # 16 (not 8) so the longest label `neutral_deferred` can't truncate.
                    max_tokens=16,
                    temperature=0.0,
                    json_mode=False,
                    caller="api.sentiment",
                    bypass_semaphore=True,
                ),
                timeout=15,
            )
            raw = str(getattr(response, "content", "")).strip().lower()
            # Extract the first recognizable word
            for word in raw.split():
                cleaned = word.strip("\"'.,:;!?")
                if cleaned in (
                    "strong_positive",
                    "weak_positive",
                    "negative",
                    "neutral",
                    "neutral_deferred",
                ):
                    logger.info("Sentiment LLM for '%s': %s (raw=%r)", domain, cleaned, raw)
                    return cleaned
            logger.info(
                "Sentiment LLM for '%s': unrecognized (raw=%r), trying keywords", domain, raw
            )
            return "neutral"
        except Exception:
            logger.info("Sentiment LLM for '%s' failed, trying keywords", domain)
            return "neutral"

    def _confirm_speculation_with_source(
        speculator: Any,
        domain: str,
        *,
        confirmation_source: str,
    ) -> bool:
        confirm = getattr(speculator, "user_confirm_speculation", None)
        if not callable(confirm):
            return False
        try:
            return bool(confirm(domain, confirmation_source=confirmation_source))
        except TypeError:
            return bool(confirm(domain))

    def _promote_exploration_buffer_entries(
        promoted: list[dict[str, object]],
    ) -> None:
        if not promoted:
            return
        from openbiliclaw.soul.interest_writeback import merge_confirmed_interest
        from openbiliclaw.soul.profile import OnionProfile

        memory_manager = getattr(ctx, "memory_manager", None)
        get_layer = getattr(memory_manager, "get_layer", None)
        if not callable(get_layer):
            return
        try:
            soul_layer = get_layer("soul")
            raw_profile = getattr(soul_layer, "data", {})
            profile = (
                OnionProfile.from_dict(raw_profile)
                if isinstance(raw_profile, dict) and raw_profile
                else OnionProfile()
            )
            changed = False
            for entry in promoted:
                raw_specifics = entry.get("specifics", [])
                specifics = (
                    [str(item) for item in raw_specifics if str(item).strip()]
                    if isinstance(raw_specifics, list)
                    else []
                )
                changed = (
                    merge_confirmed_interest(
                        profile,
                        domain=str(entry.get("domain", "")),
                        specifics=specifics,
                        source=str(entry.get("confirmation_source", "buffer_promoted")),
                        first_seen=str(entry.get("first_seen", "")),
                        last_seen=str(entry.get("last_seen", "")),
                    )
                    or changed
                )
            if not changed:
                return
            if isinstance(raw_profile, dict):
                raw_profile.clear()
                raw_profile.update(profile.to_dict())
            save = getattr(soul_layer, "save", None)
            if callable(save):
                save()
            sync_profile_files = getattr(memory_manager, "sync_profile_files", None)
            if callable(sync_profile_files):
                sync_profile_files(profile)
        except Exception:
            logger.exception("Failed to promote exploration buffer entries")

    def _record_exploration_buffer_event(
        *,
        domain: str,
        source_event: str,
        specifics: list[str] | None = None,
        evidence_id: str = "",
    ) -> None:
        from datetime import UTC, datetime

        from openbiliclaw.soul.exploration_buffer import (
            pop_promotable_buffer_entries,
            record_buffer_event,
        )

        clean_domain = domain.strip()
        if not clean_domain:
            return
        memory_manager = getattr(ctx, "memory_manager", None)
        load_state = getattr(memory_manager, "load_discovery_runtime_state", None)
        save_state = getattr(memory_manager, "save_discovery_runtime_state", None)
        update_state = getattr(memory_manager, "update_discovery_runtime_state", None)
        if not callable(update_state) and (not callable(load_state) or not callable(save_state)):
            return
        try:
            now = datetime.now(UTC)

            promoted: list[dict[str, object]] = []

            def _mutate(state: dict[str, object]) -> None:
                nonlocal promoted
                raw_buffer_state = state.get("short_term_exploration_buffer", {})
                existing_buffer_state = (
                    raw_buffer_state if isinstance(raw_buffer_state, dict) else {}
                )
                buffer_state = record_buffer_event(
                    existing_buffer_state,
                    domain=clean_domain,
                    source_event=source_event,
                    specifics=specifics or [],
                    evidence_id=evidence_id,
                    now=now,
                )
                promoted, buffer_state = pop_promotable_buffer_entries(buffer_state, now=now)
                state["short_term_exploration_buffer"] = buffer_state

            if callable(update_state):
                update_state(_mutate)
            else:
                load_state_fn = cast("Callable[[], dict[str, object]]", load_state)
                save_state_fn = cast("Callable[[dict[str, object]], None]", save_state)
                state = load_state_fn()
                if not isinstance(state, dict):
                    state = {}
                _mutate(state)
                save_state_fn(state)
            _promote_exploration_buffer_entries(promoted)
        except Exception:
            logger.exception("Failed to record exploration buffer event")

    def _recommendation_buffer_domain(row: dict[str, object]) -> tuple[str, list[str]]:
        title = str(row.get("title", "")).strip()
        domain = (
            str(row.get("topic_group", "")).strip()
            or str(row.get("topic_label", "")).strip()
            or str(row.get("topic", "")).strip()
            or str(row.get("topic_key", "")).strip()
            or title
        )
        specifics = [title] if title and title != domain else []
        return domain, specifics

    def _contextual_chat_message(turn: ChatTurnOut) -> str:
        binding = _binding_from_turn(turn)
        if binding is not None and binding.mode.value == "bound":
            # Bound context supplies the sole readable wrapper.  Keeping the
            # durable message raw prevents confusion replies from receiving a
            # second legacy scope prefix.
            return turn.message
        if turn.scope == "delight":
            label = turn.subject_title or turn.subject_id or "这条惊喜推荐"
            return f"[关于惊喜推荐「{label}」的反馈] {turn.message}"
        if turn.scope == "probe":
            label = turn.subject_title or turn.subject_id or "这个方向"
            return f"[关于猜测兴趣「{label}」的反馈] {turn.message}"
        if turn.scope == "avoidance_probe":
            label = turn.subject_title or turn.subject_id or "这个避雷方向"
            return f"[关于避雷方向「{label}」的反馈] {turn.message}"
        if turn.scope == "confusion":
            label = turn.subject_title or turn.subject_id or "这个我没太看懂的地方"
            return f"[关于我有点困惑的「{label}」的澄清] {turn.message}"
        return turn.message

    @asynccontextmanager
    async def _dialogue_execution_lease() -> AsyncIterator[Any]:
        """Hold the app-stable dialogue lease and then resolve current ctx."""
        async with dialogue_execution_coordinator.lease():
            current_dialogue = getattr(ctx, "dialogue", None)
            if current_dialogue is None:
                raise RuntimeError("Dialogue service is not configured.")
            concurrency = getattr(getattr(ctx, "discovery_engine", None), "_concurrency", None)
            if concurrency is not None:
                concurrency.chat_active = True
            try:
                yield current_dialogue
            finally:
                if concurrency is not None:
                    concurrency.chat_active = False

    async def _run_with_dialogue_execution(
        operation: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        """Run response and its ctx-dependent side effects under one lease."""
        async with _dialogue_execution_lease() as current_dialogue:
            return await operation(current_dialogue)

    async def _ensure_confusion_dialogue_anchor(turn: ChatTurnOut) -> None:
        """Queue the claim and anchor before this reply can admit its learn job."""
        if turn.scope != "confusion":
            return
        confusion_manager = getattr(ctx.soul_engine, "_confusion_manager", None)
        if confusion_manager is None:
            return
        try:
            confusion_id = int(turn.subject_id)
        except (TypeError, ValueError):
            logger.warning("Cannot establish confusion anchor for subject_id=%r", turn.subject_id)
            return
        confusion = confusion_manager.get(confusion_id)
        if confusion is None or confusion.status in {"resolved", "dismissed", "expired"}:
            return
        from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind

        if confusion.status == "open":
            scheduled = await _submit_dialogue_settlement_required(
                DialogueJobKind.CONFUSION_OPEN_SYNC,
                {
                    "operation": "schedule",
                    "confusion_id": confusion_id,
                    "ask_turn_id": turn.turn_id,
                    "asked_at": datetime.now(UTC).isoformat(),
                    "ignore_cooldown": False,
                },
            )
            claimed = bool((scheduled.settlement or {}).get("claimed", False))
            if not claimed:
                raise RuntimeError("Confusion clarifying slot is not available")
            confusion = confusion_manager.get(confusion_id)
        if confusion is None or confusion.status != "clarifying":
            raise RuntimeError("Confusion could not enter clarifying state")
        from openbiliclaw.soul.dialogue_anchor import ENTRY_CONFUSION_PROMPT

        await _submit_dialogue_settlement_required(
            DialogueJobKind.ANCHOR_ESTABLISH,
            {
                "target_kind": "confusion",
                "target_ref": str(confusion_id),
                "origin_turn_id": confusion.ask_turn_id or turn.turn_id,
                "entry": ENTRY_CONFUSION_PROMPT,
                "producer_source": "durable_confusion_ensure",
            },
        )

    async def _generate_durable_chat_reply(turn: ChatTurnOut, dialogue_owner: Any) -> str:
        respond_kwargs: dict[str, object] = {
            "scope": turn.scope or "chat",
            "turn_id": turn.turn_id,
        }
        binding = _binding_from_turn(turn)
        respond_parameters: Mapping[str, inspect.Parameter] = {}
        try:
            respond_parameters = inspect.signature(dialogue_owner.respond).parameters
        except (TypeError, ValueError):
            respond_parameters = {}
        if "session" in respond_parameters:
            respond_kwargs["session"] = turn.session
        if binding is not None and "dialogue_binding" in respond_parameters:
            respond_kwargs["dialogue_binding"] = binding
        reply = str(
            await asyncio.wait_for(
                dialogue_owner.respond(
                    _contextual_chat_message(turn),
                    **respond_kwargs,
                ),
                timeout=120,
            )
        )

        if not reply.strip():
            from openbiliclaw.llm.service import LLMResponseContentError

            raise LLMResponseContentError("LLM returned an empty response")

        return reply

    async def _apply_durable_chat_success_side_effects(turn: ChatTurnOut, reply: str) -> None:
        if turn.scope == "delight":
            label = turn.subject_title or turn.subject_id
            _record_probe_cognition(
                f"关于惊喜推荐「{label}」你说：{turn.message}",
                turn.subject_id or label,
                "delight_chat",
                detail=f"你的反馈：{turn.message}\n阿b的回复：{reply}",
            )
            await _publish_probe_event(
                "delight.chat",
                f"关于「{label}」你说：{turn.message}",
                turn.subject_id or label,
            )
        elif turn.scope == "probe":
            domain = turn.subject_id or turn.subject_title
            from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind

            completion = await _submit_dialogue_settlement_required(
                DialogueJobKind.PROBE_REPLY_APPLY,
                {
                    "turn_id": turn.turn_id,
                    "domain": domain,
                    "message": turn.message,
                    "reply": reply,
                },
            )
            intent = completion.exploration_intent
            if intent is not None:
                queue = _dialogue_settlement_queue()
                if asyncio.current_task() is getattr(queue, "worker_task", None):
                    raise RuntimeError("Exploration handoff cannot run in the settlement worker")
                _record_exploration_buffer_event(
                    domain=intent.domain,
                    source_event=intent.source_event,
                    specifics=list(intent.specifics),
                    evidence_id=intent.evidence_id,
                )
        elif turn.scope == "avoidance_probe":
            domain = turn.subject_id or turn.subject_title
            sentiment, classifier = await _classify_probe_sentiment(turn.message, reply, domain)
            speculator = getattr(ctx.soul_engine, "_avoidance_speculator", None)
            if sentiment == "negative":
                chat_response = "avoidance_chat_confirmed"
                resulting_action = "confirmed"
                if speculator is not None:
                    with suppress(Exception):
                        speculator.observe(
                            [
                                {
                                    "event_type": "dislike",
                                    "title": domain,
                                    "metadata": {
                                        "feedback_type": "dislike",
                                        "user_message": turn.message,
                                        "source": "avoidance_probe_chat",
                                    },
                                }
                            ]
                        )
                summary = f"你确认「{domain}」偏向不喜欢，确认度 +1。"
            elif sentiment in {"strong_positive", "weak_positive"}:
                chat_response = "avoidance_chat_rejected"
                resulting_action = "rejected"
                if speculator is not None:
                    reject_fn = getattr(speculator, "user_reject_avoidance", None)
                    if callable(reject_fn):
                        with suppress(Exception):
                            reject_fn(domain, cooldown_days=14)
                summary = f"你表示其实不排斥「{domain}」，已暂时搁置 14 天。"
            elif sentiment == "neutral_deferred":
                defer_outcome = "deferred"
                if speculator is not None:
                    defer_fn = getattr(speculator, "user_defer_avoidance", None)
                    if callable(defer_fn):
                        with suppress(Exception):
                            defer_outcome = defer_fn(domain).outcome
                if defer_outcome == "exhausted":
                    chat_response = "defer_exhausted"
                    resulting_action = "defer_exhausted"
                    summary = f"你多次想把避雷方向「{domain}」放一放，之后先不提了。"
                else:
                    chat_response = "defer"
                    resulting_action = "deferred"
                    summary = f"你想把避雷方向「{domain}」先放一放，过阵子再聊。"
            else:
                chat_response = "avoidance_chat_neutral"
                resulting_action = "none"
                summary = f"关于避雷方向「{domain}」你说：{turn.message}"
            if speculator is not None:
                _record_probe_feedback_history(
                    domain,
                    chat_response,
                    speculator=speculator,
                    message=turn.message,
                    classification=sentiment,
                    classifier=classifier,
                    resulting_action=resulting_action,
                    state_key="avoidance_probe_feedback_history",
                    metadata_fn=lambda item_domain: _probe_metadata_from_active_avoidance(
                        speculator,
                        item_domain,
                    ),
                )
            _record_probe_cognition(
                summary,
                domain,
                "chat",
                source="avoidance_probe",
                detail=f"你的反馈：{turn.message}\n阿b的回复：{reply}",
            )
            await _publish_probe_event("avoidance.chat", summary, domain)
        elif turn.scope == "confusion":
            from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind

            binding = _binding_from_turn(turn)
            await _submit_dialogue_settlement_required(
                DialogueJobKind.CONFUSION_REPLY_APPLY,
                {
                    "turn_id": turn.turn_id,
                    "subject_id": turn.subject_id,
                    "subject_title": turn.subject_title,
                    "message": turn.message,
                    "reply": reply,
                    "dialogue_binding": binding.to_mapping() if binding is not None else {},
                },
            )

    def _hypothesis_card_payload(payload: ChatTurnIn) -> dict[str, object]:
        raw_evidence = payload.payload.get("evidence_refs", [])
        evidence_refs = (
            [str(item).strip() for item in raw_evidence if str(item).strip()]
            if isinstance(raw_evidence, list)
            else []
        )
        return {
            "type": "card",
            "kind": "hypothesis",
            "ref": payload.subject_id.strip(),
            "title": payload.subject_title.strip(),
            "evidence_refs": evidence_refs,
            "actions": list(_HYPOTHESIS_CARD_ACTIONS),
            "state": "pending",
        }

    async def _settle_hypothesis(
        *,
        ref: str,
        hypothesis: str,
        requested_verdict: str,
        turn_id: str,
        source: str,
    ) -> dict[str, Any]:
        from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind

        completion = await _submit_dialogue_settlement(
            DialogueJobKind.SETTLE_HYPOTHESIS,
            {
                "ref": ref,
                "hypothesis": hypothesis,
                "requested_verdict": requested_verdict,
                "turn_id": turn_id,
                "source": source,
                "derived": [],
                "target_kind": "hypothesis",
                "target_ref": ref,
            },
        )
        if completion is None:
            return {
                "ok": False,
                "outcome": "processing",
                "verdict": requested_verdict,
                "state": "processing",
                "settlement_ref": ref,
            }
        if completion.settlement is not None:
            return dict(completion.settlement)
        return {"outcome": completion.outcome}

    def _defer_hypothesis_card(turn: ChatTurnOut) -> dict[str, Any]:
        _require_dialogue_settlement_worker()
        state = str(turn.payload.get("state", "")).strip().lower()
        if state in _TERMINAL_CARD_STATES:
            return {
                "ok": True,
                "outcome": "already_settled",
                "verdict": state,
                "state": state,
            }
        anchor_manager = getattr(ctx.soul_engine, "_dialogue_anchor_manager", None)
        active_anchor = anchor_manager.current() if anchor_manager is not None else None
        if (
            state == "discussing"
            and anchor_manager is not None
            and active_anchor is not None
            and active_anchor.origin_turn_id == turn.turn_id
        ):
            anchor_manager.release(
                reason="settled",
                card_state="deferred",
                expected_generation=active_anchor.generation,
            )
        elif state in {"pending", "discussing"}:
            update_payload = _chat_db_method("update_chat_turn_payload_state")
            if update_payload is None or not bool(
                update_payload(
                    turn.turn_id,
                    expected_state=state,
                    new_state="deferred",
                )
            ):
                raise HTTPException(
                    status_code=409, detail="Card state changed; refresh and retry."
                )
            # A pending-open card establishes its dialogue anchor while the
            # card itself intentionally remains ``pending``. Deferring that
            # card must release the same exact generation too; otherwise the
            # invisible old anchor blocks every later pending item with 409.
            if (
                state == "pending"
                and anchor_manager is not None
                and active_anchor is not None
                and active_anchor.origin_turn_id == turn.turn_id
            ):
                anchor_manager.release(
                    reason="settled",
                    card_state="deferred",
                    expected_generation=active_anchor.generation,
                )
        deferred_until = _defer_dialogue_confirmation(str(turn.payload.get("ref", "")))
        return {
            "ok": True,
            "outcome": "deferred",
            "verdict": "deferred",
            "state": "deferred",
            "deferred_until": deferred_until,
        }

    def _anchor_actual_state(kind: str, ref: str) -> Any:
        from openbiliclaw.soul.dialogue_learn_queue import AnchorAbsent, AnchorPersisted

        anchor_manager = getattr(ctx.soul_engine, "_dialogue_anchor_manager", None)
        if anchor_manager is None:
            return AnchorAbsent(target_kind=kind, target_ref=ref, tombstone_epoch=1)
        active = anchor_manager.current()
        if active is not None and active.kind == kind and active.ref == ref:
            return AnchorPersisted(
                kind=active.kind,
                ref=active.ref,
                generation=active.generation,
            )
        return AnchorAbsent(target_kind=kind, target_ref=ref, tombstone_epoch=1)

    def _discuss_hypothesis_card(
        turn: ChatTurnOut,
        job: DialogueJob,
    ) -> DialogueDispatchResult:
        """Apply card discuss in-worker and return before reservation resolution."""
        from types import MappingProxyType

        from openbiliclaw.soul.dialogue_anchor import ENTRY_CARD_DISCUSS
        from openbiliclaw.soul.dialogue_learn_queue import (
            AnchorMutationTerminal,
            DialogueDispatchResult,
            DialogueJobResult,
        )

        _require_dialogue_settlement_worker()
        if job.owned_anchor_reservation_id is None:
            raise RuntimeError("card.discuss reached worker without an anchor reservation")
        anchor_manager = getattr(ctx.soul_engine, "_dialogue_anchor_manager", None)
        if anchor_manager is None:
            raise RuntimeError("Dialogue anchor manager not ready")
        ref = str(turn.payload.get("ref", "")).strip()
        state = str(turn.payload.get("state", ""))
        if state not in {"pending", "discussing"}:
            result = {
                "ok": True,
                "outcome": "already_settled",
                "verdict": state,
                "state": state,
            }
            return DialogueDispatchResult(
                result=DialogueJobResult(
                    outcome="already_settled",
                    settlement=MappingProxyType(result),
                ),
                anchor_terminal=AnchorMutationTerminal.already_terminal(
                    _anchor_actual_state("hypothesis", ref),
                ),
            )
        if state == "pending":
            update_payload = _chat_db_method("update_chat_turn_payload_state")
            if update_payload is None or not bool(
                update_payload(
                    turn.turn_id,
                    expected_state="pending",
                    new_state="discussing",
                )
            ):
                raise RuntimeError("Card state changed before discuss apply")
        response = {
            "ok": True,
            "outcome": "discussing",
            "verdict": "discussing",
            "state": "discussing",
        }
        existing = anchor_manager.current()
        try:
            established = anchor_manager.establish(
                kind="hypothesis",
                ref=ref,
                origin_turn_id=turn.turn_id,
                entry=ENTRY_CARD_DISCUSS,
            )
        except Exception as exc:
            failure = exc
            terminal = AnchorMutationTerminal.failed(
                _anchor_actual_state("hypothesis", ref),
                cause=f"{type(failure).__name__}: {failure}",
            )

            async def _rollback_failed_discuss() -> None:
                update_payload = _chat_db_method("update_chat_turn_payload_state")
                if update_payload is not None:
                    update_payload(
                        turn.turn_id,
                        expected_state="discussing",
                        new_state="pending",
                    )
                raise failure

            return DialogueDispatchResult(
                result=DialogueJobResult(outcome="failed"),
                anchor_terminal=terminal,
                followup=_rollback_failed_discuss,
            )
        terminal = (
            AnchorMutationTerminal.no_op(
                _anchor_actual_state("hypothesis", ref),
            )
            if (
                existing is not None
                and existing.kind == established.kind
                and existing.ref == established.ref
            )
            else AnchorMutationTerminal.persisted(
                kind=established.kind,
                ref=established.ref,
                generation=established.generation,
            )
        )
        return DialogueDispatchResult(
            result=DialogueJobResult(
                outcome="discussing",
                settlement=MappingProxyType(response),
            ),
            anchor_terminal=terminal,
        )

    async def _handle_card_defer(job: DialogueJob) -> DialogueJobResult:
        from types import MappingProxyType

        from openbiliclaw.soul.dialogue_learn_queue import DialogueJobResult

        row = _read_chat_turn_row(str(job.payload.get("turn_id", "")))
        if row is None:
            raise RuntimeError("Card disappeared before defer apply")
        result = _defer_hypothesis_card(_normalize_chat_turn(row))
        return DialogueJobResult(
            outcome=str(result["outcome"]),
            settlement=MappingProxyType(result),
        )

    async def _handle_card_discuss(job: DialogueJob) -> DialogueDispatchResult:
        row = _read_chat_turn_row(str(job.payload.get("turn_id", "")))
        if row is None:
            raise RuntimeError("Card disappeared before discuss apply")
        return _discuss_hypothesis_card(_normalize_chat_turn(row), job)

    async def _handle_anchor_establish(job: DialogueJob) -> DialogueDispatchResult:
        from openbiliclaw.soul.dialogue_learn_queue import (
            AnchorMutationTerminal,
            DialogueDispatchResult,
            DialogueJobResult,
        )

        _require_dialogue_settlement_worker()
        if job.owned_anchor_reservation_id is None:
            raise RuntimeError("anchor.establish reached worker without a reservation")
        kind = str(job.payload.get("target_kind", "")).strip()
        ref = str(job.payload.get("target_ref", "")).strip()
        origin_turn_id = str(job.payload.get("origin_turn_id", "")).strip()
        entry = str(job.payload.get("entry", "")).strip()
        anchor_manager = getattr(ctx.soul_engine, "_dialogue_anchor_manager", None)
        if anchor_manager is None:
            raise RuntimeError("Dialogue anchor manager not ready")
        existing = anchor_manager.current()
        established = anchor_manager.establish(
            kind=kind,
            ref=ref,
            origin_turn_id=origin_turn_id,
            entry=entry,
        )
        terminal = (
            AnchorMutationTerminal.no_op(
                _anchor_actual_state(kind, ref),
            )
            if (
                existing is not None
                and existing.kind == established.kind
                and existing.ref == established.ref
            )
            else AnchorMutationTerminal.persisted(
                kind=established.kind,
                ref=established.ref,
                generation=established.generation,
            )
        )
        return DialogueDispatchResult(
            result=DialogueJobResult(outcome=terminal.disposition.value),
            anchor_terminal=terminal,
        )

    async def _handle_confusion_open_sync(job: DialogueJob) -> DialogueJobResult:
        from types import MappingProxyType

        from openbiliclaw.soul.dialogue_learn_queue import DialogueJobResult

        _require_dialogue_settlement_worker()
        operation = str(job.payload.get("operation", "")).strip().lower()
        if operation not in {"schedule", "retarget", "rollback", "reconcile_orphan"}:
            raise ValueError(f"Unsupported confusion.open.sync operation: {operation!r}")
        try:
            confusion_id = int(str(job.payload.get("confusion_id", 0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("confusion.open.sync requires an integer confusion_id") from exc
        result: dict[str, object] = {"operation": operation, "confusion_id": confusion_id}
        if operation == "schedule":
            confusion_manager = getattr(ctx.soul_engine, "_confusion_manager", None)
            if confusion_manager is None:
                raise RuntimeError("Confusion manager not ready")
            asked_at_raw = str(job.payload.get("asked_at", "")).strip()
            asked_at = datetime.fromisoformat(asked_at_raw) if asked_at_raw else datetime.now(UTC)
            result["claimed"] = bool(
                confusion_manager.schedule_ask(
                    confusion_id,
                    ask_turn_id=str(job.payload.get("ask_turn_id", "")),
                    now=asked_at,
                    ignore_cooldown=bool(job.payload.get("ignore_cooldown", False)),
                )
            )
        elif operation == "reconcile_orphan":
            releaser = getattr(ctx.database, "release_orphan_confusion_claim", None)
            if not callable(releaser):
                raise RuntimeError("Confusion orphan recovery is not ready")
            result["released"] = bool(
                releaser(
                    confusion_id,
                    expected_ask_turn_id=str(job.payload.get("expected_ask_turn_id", "")).strip(),
                    minimum_age_seconds=float(
                        cast(
                            "Any",
                            job.payload.get(
                                "minimum_age_seconds",
                                _CONFUSION_ORPHAN_CLAIM_MIN_AGE_SECONDS,
                            ),
                        )
                    ),
                )
            )
        else:
            updater = getattr(ctx.database, "update_confusion", None)
            if not callable(updater):
                raise RuntimeError("Confusion store is not ready")
            if operation == "retarget":
                updater(
                    confusion_id,
                    ask_turn_id=str(job.payload.get("ask_turn_id", "")),
                    asked_at=str(job.payload.get("asked_at", "")),
                )
            else:
                updater(confusion_id, status="open", ask_turn_id="")
            result["updated"] = True
        return DialogueJobResult(
            outcome="completed",
            settlement=MappingProxyType(result),
        )

    def _chat_turn_effect_receipt(turn_id: str, receipt_key: str) -> dict[str, object] | None:
        row = _read_chat_turn_row(turn_id)
        if row is None:
            return None
        raw_payload = row.get("payload", {})
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        raw_receipt = payload.get(receipt_key)
        return dict(raw_receipt) if isinstance(raw_receipt, dict) else None

    def _store_chat_turn_effect_receipt(
        turn_id: str,
        receipt_key: str,
        receipt: dict[str, object],
    ) -> None:
        store = _chat_db_method("store_chat_turn_effect_receipt")
        if store is None or not bool(
            store(
                turn_id,
                receipt_key=receipt_key,
                receipt=receipt,
            )
        ):
            raise RuntimeError(f"Could not persist {receipt_key} for turn {turn_id!r}")

    async def _handle_probe_reply_apply(job: DialogueJob) -> DialogueJobResult:
        from openbiliclaw.soul.dialogue_learn_queue import (
            DialogueJobResult,
            ExplorationIntent,
        )

        _require_dialogue_settlement_worker()
        turn_id = str(job.payload.get("turn_id", "")).strip()
        domain = str(job.payload.get("domain", "")).strip()
        message = str(job.payload.get("message", ""))
        reply = str(job.payload.get("reply", ""))
        if not turn_id or not domain:
            raise ValueError("probe.reply.apply requires turn_id and domain")
        receipt_key = "probe_reply_apply"
        receipt = _chat_turn_effect_receipt(turn_id, receipt_key)
        speculator: Any = getattr(ctx.soul_engine, "_speculator", None)
        if receipt is None:
            sentiment, classifier = await _classify_probe_sentiment(message, reply, domain)
            metadata: dict[str, object] = (
                _probe_metadata_from_active_speculation(speculator, domain)
                if speculator is not None
                else {"domain": domain}
            )
            chat_response = "chat_neutral"
            resulting_action = "none"
            if sentiment == "negative":
                chat_response = "chat_rejected"
                resulting_action = "rejected"
                summary = f"你对「{domain}」的反馈偏负面（{message}），已暂时搁置 14 天。"
            elif sentiment == "strong_positive":
                chat_response = "chat_confirmed"
                resulting_action = "confirmed"
                summary = f"你明确确认了对「{domain}」的兴趣，已加入画像。"
            elif sentiment == "weak_positive":
                chat_response = "weak_positive"
                resulting_action = "weak_positive_deferred"
                summary = f"你对「{domain}」有轻微信号，先作为短期探索方向观察。"
            elif sentiment == "neutral_deferred":
                chat_response = "defer"
                resulting_action = "deferred"
                summary = f"你想把「{domain}」先放一放，过阵子再聊。"
            else:
                summary = f"关于「{domain}」你说：{message}"
            raw_specifics = metadata.get("specifics", [])
            specific_names = (
                [str(item) for item in raw_specifics if str(item).strip()]
                if isinstance(raw_specifics, list)
                else []
            )
            receipt = {
                "classification": sentiment,
                "classifier": classifier,
                "resulting_action": resulting_action,
                "chat_response": chat_response,
                "summary": summary,
                "metadata": dict(metadata),
                "specifics": specific_names,
                "effects": {
                    "settlement": False,
                    "history": False,
                    "cognition": False,
                    "event": False,
                },
            }
            _store_chat_turn_effect_receipt(turn_id, receipt_key, receipt)

        sentiment = str(receipt.get("classification", "neutral"))
        classifier = str(receipt.get("classifier", "neutral_default"))
        resulting_action = str(receipt.get("resulting_action", "none"))
        chat_response = str(receipt.get("chat_response", "chat_neutral"))
        summary = str(receipt.get("summary", f"关于「{domain}」你说：{message}"))
        raw_metadata = receipt.get("metadata", {})
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {"domain": domain}
        raw_specifics = receipt.get("specifics", [])
        specifics_tuple = (
            tuple(str(item) for item in raw_specifics if str(item).strip())
            if isinstance(raw_specifics, list)
            else ()
        )
        raw_effects = receipt.get("effects", {})
        effects = dict(raw_effects) if isinstance(raw_effects, dict) else {}

        if not bool(effects.get("settlement", False)):
            if sentiment == "negative" and speculator is not None:
                with suppress(Exception):
                    speculator.user_reject_speculation(domain, cooldown_days=14)
            elif sentiment == "strong_positive" and speculator is not None:
                with suppress(Exception):
                    _confirm_speculation_with_source(
                        speculator,
                        domain,
                        confirmation_source="chat_confirmed",
                    )
            elif sentiment == "neutral_deferred":
                defer_outcome = "deferred"
                if speculator is not None:
                    with suppress(Exception):
                        defer_outcome = speculator.user_defer_speculation(domain).outcome
                if defer_outcome == "exhausted":
                    chat_response = "defer_exhausted"
                    resulting_action = "defer_exhausted"
                    summary = f"你多次想把「{domain}」放一放，之后先不提了。"
                    receipt["chat_response"] = chat_response
                    receipt["resulting_action"] = resulting_action
                    receipt["summary"] = summary
            effects["settlement"] = True
            receipt["effects"] = effects
            _store_chat_turn_effect_receipt(turn_id, receipt_key, receipt)

        if not bool(effects.get("history", False)):
            if speculator is not None:
                _record_probe_feedback_history(
                    domain,
                    chat_response,
                    speculator=speculator,
                    message=message,
                    classification=sentiment,
                    classifier=classifier,
                    resulting_action=resulting_action,
                    metadata=metadata,
                )
            effects["history"] = True
            receipt["effects"] = effects
            _store_chat_turn_effect_receipt(turn_id, receipt_key, receipt)

        if not bool(effects.get("cognition", False)):
            _record_probe_cognition(
                summary,
                domain,
                "chat",
                detail=f"你的反馈：{message}\n阿b的回复：{reply}",
            )
            effects["cognition"] = True
            receipt["effects"] = effects
            _store_chat_turn_effect_receipt(turn_id, receipt_key, receipt)

        if not bool(effects.get("event", False)):
            await _publish_probe_event("interest.chat", summary, domain)
            effects["event"] = True
            receipt["effects"] = effects
            _store_chat_turn_effect_receipt(turn_id, receipt_key, receipt)

        exploration_intent = (
            ExplorationIntent(
                domain=domain,
                source_event="weak_positive_chat",
                specifics=specifics_tuple,
                evidence_id=turn_id,
            )
            if sentiment == "weak_positive"
            else None
        )
        return DialogueJobResult(
            outcome="applied",
            classification=sentiment,
            classifier=classifier,
            resulting_action=resulting_action,
            exploration_intent=exploration_intent,
        )

    async def _handle_confusion_reply_apply(job: DialogueJob) -> DialogueJobResult:
        from openbiliclaw.soul.dialogue_learn_queue import DialogueJobResult

        _require_dialogue_settlement_worker()
        turn_id = str(job.payload.get("turn_id", "")).strip()
        if not turn_id:
            raise ValueError("confusion.reply.apply requires turn_id")
        subject_id = str(job.payload.get("subject_id", "")).strip()
        label = str(job.payload.get("subject_title", "")).strip() or subject_id or "这个方向"
        message = str(job.payload.get("message", ""))
        reply = str(job.payload.get("reply", ""))
        raw_binding = job.payload.get("dialogue_binding")
        context_digest = ""
        if isinstance(raw_binding, Mapping):
            context_digest = str(raw_binding.get("context_digest", "")).strip()
        domain = subject_id or label
        receipt_key = "confusion_reply_apply"
        receipt = _chat_turn_effect_receipt(turn_id, receipt_key)
        if receipt is None:
            receipt = {
                "summary": f"关于「{label}」你说：{message}",
                "effects": {"cognition": False, "event": False},
                "context_digest": context_digest,
            }
            _store_chat_turn_effect_receipt(turn_id, receipt_key, receipt)
        summary = str(receipt.get("summary", f"关于「{label}」你说：{message}"))
        raw_effects = receipt.get("effects", {})
        effects = dict(raw_effects) if isinstance(raw_effects, dict) else {}
        if not bool(effects.get("cognition", False)):
            _record_probe_cognition(
                summary,
                domain,
                "chat",
                source="confusion",
                detail=f"你的反馈：{message}\n阿b的回复：{reply}",
            )
            effects["cognition"] = True
            receipt["effects"] = effects
            _store_chat_turn_effect_receipt(turn_id, receipt_key, receipt)
        if not bool(effects.get("event", False)):
            await _publish_probe_event("confusion.chat", summary, domain)
            effects["event"] = True
            receipt["effects"] = effects
            _store_chat_turn_effect_receipt(turn_id, receipt_key, receipt)
        return DialogueJobResult(outcome="applied")

    async def _handle_card_reconcile(job: DialogueJob) -> DialogueJobResult:
        from types import MappingProxyType

        from openbiliclaw.soul.dialogue_learn_queue import DialogueJobResult

        _require_dialogue_settlement_worker()
        turn_id = str(job.payload.get("turn_id", "")).strip()
        row = _read_chat_turn_row(turn_id) if turn_id else None
        repaired = _reconcile_chat_card_row(row) if row is not None else False
        reconcile = getattr(ctx.soul_engine, "_apply_card_reconcile", None)
        if not callable(reconcile):
            raise RuntimeError("card.reconcile worker handler is not ready")
        result = await reconcile(ref=str(job.payload.get("ref", "")))
        if not isinstance(result, dict):
            raise RuntimeError("card.reconcile returned an invalid result")
        if repaired and result.get("outcome") == "not_found":
            result = {
                "outcome": "reconciled",
                "state": "pending",
                "settlement_ref": str(job.payload.get("ref", "")),
            }
        return DialogueJobResult(
            outcome=str(result.get("outcome", "completed")),
            settlement=MappingProxyType(dict(result)),
        )

    from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind

    ctx.dialogue_settlement_handlers.update(
        {
            DialogueJobKind.CARD_DEFER: _handle_card_defer,
            DialogueJobKind.CARD_DISCUSS: _handle_card_discuss,
            DialogueJobKind.CARD_RECONCILE: _handle_card_reconcile,
            DialogueJobKind.ANCHOR_ESTABLISH: _handle_anchor_establish,
            DialogueJobKind.PROBE_REPLY_APPLY: _handle_probe_reply_apply,
            DialogueJobKind.CONFUSION_REPLY_APPLY: _handle_confusion_reply_apply,
            DialogueJobKind.CONFUSION_OPEN_SYNC: _handle_confusion_open_sync,
        }
    )

    # Issue #213: ``no_provider`` is a config-shaped failure (the resolved
    # module route / global chain references no chat-capable instance), so
    # retrying forever only parks the turn on the infinite "thinking" spinner
    # and head-of-line blocks every later turn. Escalate to a terminal failed
    # turn after a few fast no-provider failures (other failure kinds do not
    # reset the count — mixed transient + no-provider failures still point at
    # the same broken route); transient windows (a quick settings toggle, hot
    # reload) still get those retries to heal.
    _chat_no_provider_streak: dict[str, int] = {}

    def _escalate_persistent_no_provider_failure(turn_id: str, exc: Exception) -> bool:
        """Count no-provider failures for one durable turn; True when terminal."""
        from openbiliclaw.llm.base import classify_llm_failure_kind

        if classify_llm_failure_kind(exc) != "no_provider":
            return False
        streak = _chat_no_provider_streak.get(turn_id, 0) + 1
        _chat_no_provider_streak[turn_id] = streak
        return streak >= _CHAT_NO_PROVIDER_TERMINAL_ATTEMPTS

    async def _complete_durable_chat_turn(turn_id: str) -> None:
        row = _get_chat_turn_row(turn_id)
        if row is None or str(row.get("status", "")) != "pending":
            _chat_no_provider_streak.pop(turn_id, None)
            return
        async with _dialogue_execution_lease() as current_dialogue:
            # The turn may have completed while this worker waited behind hot
            # reload or a synchronous dialogue. Re-read under the stable lease.
            row = _get_chat_turn_row(turn_id)
            if row is None or str(row.get("status", "")) != "pending":
                _chat_no_provider_streak.pop(turn_id, None)
                return
            turn = _normalize_chat_turn(row)
            try:
                binding = _binding_from_turn(turn)
                if binding is None or binding.mode.value != "bound":
                    await _ensure_confusion_dialogue_anchor(turn)
                reply = await _generate_durable_chat_reply(turn, current_dialogue)
            except asyncio.CancelledError:
                # Shutdown leaves the durable row pending for startup recovery.
                raise
            except Exception as exc:
                from openbiliclaw.llm.service import LLMResponseContentError

                if isinstance(exc, LLMResponseContentError):
                    raise TerminalChatReplyError(
                        safe_llm_failure_message(exc),
                        code="invalid_response",
                    ) from exc
                if _escalate_persistent_no_provider_failure(turn_id, exc):
                    _chat_no_provider_streak.pop(turn_id, None)
                    raise TerminalChatReplyError(
                        "AI 模块路由连续多次解析不到任何可用实例"
                        "（常见原因：设置页的模块路由或全局调用链引用了已停用 / 已删除的实例）。"
                        "本条消息已停止等待，请修正 LLM 路由配置后重新发送。",
                        code="no_provider",
                    ) from exc
                raise
            _chat_no_provider_streak.pop(turn_id, None)

            completed = _complete_chat_turn_row(turn_id, reply=reply)
            if not completed:
                current = _get_chat_turn_row(turn_id)
                if current is not None and str(current.get("status", "")) == "pending":
                    # A genuine CAS miss means another owner already made the
                    # outcome visible. A pending row means persistence did not
                    # land and must stay at the head of the retry lane.
                    raise RuntimeError("chat turn completion CAS did not update pending row")
                return
            try:
                # Handoff effects read current ctx and therefore stay inside
                # the same lease as anchor→respond→visible completion CAS.
                await _apply_durable_chat_success_side_effects(turn, reply)
            except Exception:
                logger.exception(
                    "Failed to apply durable chat side effects for %s",
                    turn_id,
                )

    chat_reply_scheduler = DurableChatReplyScheduler(
        processor=_complete_durable_chat_turn,
        database_resolver=lambda: getattr(ctx, "database", None),
    )
    app.state.chat_reply_scheduler = chat_reply_scheduler

    @app.post("/api/chat/turns", response_model=ChatTurnOut)
    async def start_chat_turn(payload: ChatTurnIn) -> ChatTurnOut:
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Chat message is required.")
        normalized_scope = _normalize_chat_scope(payload.scope)
        if normalized_scope == "hypothesis" and (
            not payload.subject_id.strip() or not payload.subject_title.strip()
        ):
            raise HTTPException(
                status_code=422,
                detail="Hypothesis cards require subject_id and subject_title.",
            )
        raw_turn_id = payload.turn_id.strip()
        turn_id = raw_turn_id or f"turn-{uuid.uuid4().hex}"
        existing = _get_chat_turn_row(turn_id)
        if existing is not None:
            if not _stored_request_matches(existing, payload):
                _dialogue_context_error(
                    409,
                    "turn_id_conflict",
                    "This turn id already belongs to a different request.",
                )
            turn = _normalize_chat_turn(existing)
            if turn.status == "pending":
                chat_reply_scheduler.schedule(turn.turn_id)
            return turn

        if normalized_scope == "hypothesis":
            if payload.reply_to_turn_id.strip():
                _dialogue_context_error(
                    422,
                    "invalid_reply_target",
                    "A hypothesis card cannot itself reply to another turn.",
                )
            row = _create_chat_turn_row(
                payload,
                turn_id=turn_id,
                structured_payload=_hypothesis_card_payload(payload),
            )
            _complete_chat_turn_row(turn_id, reply="")
            completed = _get_chat_turn_row(turn_id)
            if completed is None:
                raise RuntimeError("Completed hypothesis card disappeared")
            return _normalize_chat_turn(completed)

        from openbiliclaw.soul.dialogue_turn_context import DialogueTurnBinding

        binding: DialogueTurnBinding
        canonical_scope = normalized_scope
        canonical_subject_id = payload.subject_id.strip()
        canonical_subject_title = payload.subject_title.strip()
        if payload.reply_to_turn_id.strip():
            context, canonical_scope, canonical_subject_id, canonical_subject_title = (
                _canonical_context_for_target(payload.reply_to_turn_id)
            )
            requested_scope = normalized_scope
            requested_subject_id = payload.subject_id.strip()
            requested_subject_title = payload.subject_title.strip()
            if context.source_type == "card":
                valid_request = (
                    requested_scope == "chat"
                    and not requested_subject_id
                    and not requested_subject_title
                )
            else:
                # ``scope=confusion`` is retained for older clients only when
                # they still point at the exact canonical question. New clients
                # use the default chat/empty-subject request shape.
                valid_request = (
                    requested_scope in {"chat", "confusion"}
                    and requested_subject_id in {"", context.ref}
                    and requested_subject_title in {"", context.title}
                )
            if not valid_request:
                _dialogue_context_error(
                    422,
                    "invalid_reply_target",
                    "The selected target conflicts with the canonical reply context.",
                )
            binding = DialogueTurnBinding.from_context(context)
        else:
            attached_confirmation: ChatTurnOut | None = None
            if normalized_scope in {"chat", "delight", "probe", "avoidance_probe"}:
                # Deterministic attachment boundary: the confirmation row is
                # committed before the user's durable row, so equal-second
                # timestamps are still ordered by SQLite rowid.
                attached_confirmation = await _maybe_attach_system_confirmation(
                    payload,
                    turn_id=turn_id,
                )
            binding = (
                DialogueTurnBinding.detached()
                if attached_confirmation is not None
                else _unbound_dialogue_binding(attached_confirmation=False)
            )

        # Everything below this line is synchronous: the binding is already a
        # canonical immutable value and must be durable before any reply task
        # can observe it.  Do not add an await between capture and INSERT.
        canonical_request = payload.model_copy(
            update={
                "scope": canonical_scope,
                "subject_id": canonical_subject_id,
                "subject_title": canonical_subject_title,
            }
        )
        structured_payload = dict(payload.payload)
        structured_payload["dialogue_binding"] = binding.to_mapping()
        row = _create_chat_turn_row(
            canonical_request,
            turn_id=turn_id,
            structured_payload=structured_payload,
        )
        chat_reply_scheduler.schedule(turn_id)
        return _normalize_chat_turn(row)

    @app.get("/api/chat/pending-confirmations", response_model=None)
    async def list_pending_confirmations(
        count_only: bool = Query(default=False),
        session: str = Query(default=""),
    ) -> dict[str, Any]:
        # Reconciliation is a local queue mutation. A list read must remain
        # available while a long LLM settlement owns the worker; the orphan can
        # be reconciled on the next idle read/open instead of blocking the UI.
        if _dialogue_queue_ready_for_interactive_submission():
            await _reconcile_orphan_confusion_claims()
        items = _pending_confirmation_items(
            limit=_PENDING_CONFIRMATION_LIMIT,
            session=session,
        )
        if count_only:
            return {"count": len(items)}
        return {"count": len(items), "items": items}

    @app.post("/api/chat/pending-confirmations/{ref}/open", response_model=ChatTurnOut)
    async def open_pending_confirmation(
        ref: str,
        payload: Annotated[dict[str, object], Body()],
    ) -> ChatTurnOut:
        if not _dialogue_queue_ready_for_interactive_submission():
            _raise_dialogue_busy()
        await _reconcile_orphan_confusion_claims()
        if not _dialogue_queue_ready_for_interactive_submission():
            _raise_dialogue_busy()
        item = _pending_confirmation_by_ref(ref)
        if item is None:
            raise HTTPException(status_code=404, detail="Pending confirmation not found.")
        session = str(payload.get("session", "popup")).strip() or "popup"
        turn, _created = await _create_confirmation_turn(
            item,
            session=session,
            user_initiated=True,
        )
        return turn

    @app.post("/api/chat/cards/{turn_id}/action", response_model=None)
    async def act_on_chat_card(
        turn_id: str,
        payload: Annotated[dict[str, object], Body()],
    ) -> Response | dict[str, Any]:
        action = str(payload.get("action", "")).strip().lower()
        if action not in _HYPOTHESIS_CARD_ACTIONS:
            raise HTTPException(status_code=422, detail="Unsupported card action.")
        row = _get_chat_turn_row(turn_id.strip())
        if row is None:
            raise HTTPException(status_code=404, detail="Chat card not found.")
        turn = _normalize_chat_turn(row)
        if turn.payload.get("type") != "card" or turn.payload.get("kind") != "hypothesis":
            raise HTTPException(status_code=409, detail="Chat turn is not a hypothesis card.")
        if action in {"defer", "discuss"}:
            from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind

            ref = str(turn.payload.get("ref", "")).strip()
            kind = DialogueJobKind.CARD_DEFER if action == "defer" else DialogueJobKind.CARD_DISCUSS
            completion = await _submit_dialogue_settlement(
                kind,
                {
                    "turn_id": turn.turn_id,
                    "action": action,
                    "target_kind": "hypothesis",
                    "target_ref": ref,
                    "producer_source": "card_action",
                },
            )
            if completion is None:
                return JSONResponse(
                    status_code=202,
                    content={
                        "ok": False,
                        "outcome": "processing",
                        "verdict": action,
                        "state": "processing",
                        "settlement_ref": ref,
                    },
                )
            result: dict[str, Any] = (
                dict(completion.settlement)
                if completion.settlement is not None
                else {"outcome": completion.outcome}
            )
            if result.get("outcome") == "processing":
                return JSONResponse(status_code=202, content=result)
            if action == "discuss" and result.get("outcome") == "discussing":
                try:
                    context, _scope, _subject_id, _subject_title = _canonical_context_for_target(
                        turn.turn_id
                    )
                    from openbiliclaw.soul.dialogue_turn_context import DialogueTurnBinding

                    result["context_preview"] = _context_preview(
                        DialogueTurnBinding.from_context(context)
                    ).model_dump()
                except HTTPException:
                    logger.warning(
                        "card.discuss completed without a readable context preview: %s",
                        turn.turn_id,
                        exc_info=True,
                    )
            return result
        result = await _settle_hypothesis(
            ref=str(turn.payload.get("ref", "")).strip(),
            hypothesis=str(turn.payload.get("title", "")).strip(),
            requested_verdict=action,
            turn_id=turn.turn_id,
            source="card_action",
        )
        if result.get("outcome") == "processing":
            return JSONResponse(status_code=202, content=result)
        return result

    @app.get(
        "/api/chat/contexts/{reply_to_turn_id}",
        response_model=DialogueContextPreview,
    )
    async def get_chat_context(reply_to_turn_id: str) -> DialogueContextPreview:
        """Return a canonical target preview without admitting any queue work."""
        context, _scope, _subject_id, _subject_title = _canonical_context_for_target(
            reply_to_turn_id
        )
        from openbiliclaw.soul.dialogue_turn_context import DialogueTurnBinding

        return _context_preview(DialogueTurnBinding.from_context(context))

    @app.get("/api/chat/turns", response_model=ChatTurnListResponse)
    async def list_chat_turns(
        session: str = "popup",
        scope: str = "",
        limit: int = Query(default=50, ge=1, le=200),
    ) -> ChatTurnListResponse:
        normalized_scope = _normalize_chat_scope(scope) if scope else ""
        rows = _list_chat_turn_rows(
            session=session.strip() or "popup",
            scope=normalized_scope,
            limit=limit,
        )
        for row in rows:
            _submit_chat_card_reconcile(row)
        return ChatTurnListResponse(items=[_normalize_chat_turn(row) for row in rows])

    @app.get("/api/chat/turns/{turn_id}", response_model=ChatTurnOut)
    async def get_chat_turn(turn_id: str) -> ChatTurnOut:
        row = _read_chat_turn_row(turn_id.strip())
        if row is None:
            raise HTTPException(status_code=404, detail="Chat turn not found.")
        turn = _normalize_chat_turn(row)
        _submit_chat_card_reconcile(row)
        if turn.status == "pending":
            chat_reply_scheduler.schedule(turn.turn_id)
        return turn

    @app.post("/api/interest-probes/trigger")
    async def trigger_interest_probe() -> dict[str, Any]:
        """Manually trigger an interest probe push via WebSocket.

        Useful when ``run_forever`` is blocked by a long refresh cycle
        and the probe wouldn't fire on its own for several minutes.
        """
        controller = ctx.runtime_controller
        if controller is None:
            raise HTTPException(status_code=503, detail="Runtime controller not available")
        publish = getattr(controller, "_publish_interest_probe_if_available", None)
        if not callable(publish):
            raise HTTPException(status_code=503, detail="Probe publisher not available")
        await publish()
        return {"ok": True, "action": "probe_triggered"}

    @app.get("/api/interest-probes/pending")
    async def pending_interest_probes() -> dict[str, Any]:
        """Return active speculative interests that the user hasn't responded to.

        The mobile web UI polls this on page load / bell-click so probes
        survive page refreshes (unlike WebSocket-only delivery).
        """
        try:
            from openbiliclaw.soul.speculator import load_speculative_state

            runtime_config = getattr(ctx, "config", None) or config
            spec_state = load_speculative_state(runtime_config.data_path)
            active = [item for item in spec_state.active if item.status == "active"]
            items = []
            for item in active[:6]:
                probe_mode, challenge = _probe_metadata_for_payload(item)
                items.append(
                    {
                        "domain": item.domain,
                        "reason": item.reason,
                        "confidence": item.confidence,
                        "status": item.status,
                        "probe_mode": probe_mode,
                        "challenge": challenge,
                    }
                )
            return {"items": items}
        except Exception:
            return {"items": []}

    @app.post("/api/interest-probes/respond")
    async def respond_to_interest_probe(payload: dict[str, Any]) -> Any:
        """User responds to a speculated interest probe.

        Body: { "domain": "...", "response": "confirm" | "reject" | "defer" | "chat", ... }

        - confirm: Force-promote the speculation
        - reject: Move to cooldown (30 days)
        - defer: Snooze the probe (暂时忽略); escalates on repeat, exhausts to cooldown
        - chat: Forward to dialogue engine with probe context, return reply
        """
        domain = str(payload.get("domain", "")).strip()
        response_type = str(payload.get("response", "")).strip().lower()

        if not domain:
            raise HTTPException(status_code=422, detail="domain is required")
        if response_type not in {"confirm", "reject", "defer", "chat"}:
            raise HTTPException(
                status_code=422, detail="response must be confirm, reject, defer, or chat"
            )

        speculator: Any = getattr(ctx.soul_engine, "_speculator", None)
        if response_type != "chat" and speculator is None:
            raise HTTPException(status_code=503, detail="Speculator not available")

        if response_type == "confirm":
            requested_source = str(payload.get("confirmation_source", "")).strip()
            surface = str(payload.get("surface", "")).strip().lower()
            confirmation_source = requested_source or (
                "profile_confirmed" if surface == "profile" else "probe_confirmed"
            )
            metadata = _probe_metadata_from_active_speculation(speculator, domain)
            ok = _confirm_speculation_with_source(
                speculator,
                domain,
                confirmation_source=confirmation_source,
            )
            if ok:
                _record_probe_feedback_history(
                    domain,
                    "confirm",
                    speculator=speculator,
                    resulting_action="confirmed",
                    metadata=metadata,
                )
                # Force_tick generates 5 new probes via LLM (~30-60s).
                # Running it inline blocks the response past the
                # browser fetch timeout (35s) — the user gives up,
                # AbortError fires, and the next click hits a stale UI.
                # Schedule it as a background task so the API returns
                # immediately; the new probes will be visible on the
                # next profile-summary refresh.
                tick_fn = getattr(speculator, "force_tick", None)
                if callable(tick_fn):

                    async def _bg_force_tick() -> None:
                        try:
                            profile = await ctx.soul_engine.get_profile()
                            feedback_history: object = []
                            load_runtime_state = getattr(
                                ctx.memory_manager,
                                "load_discovery_runtime_state",
                                None,
                            )
                            if callable(load_runtime_state):
                                runtime_state = load_runtime_state()
                                if isinstance(runtime_state, dict):
                                    feedback_history = runtime_state.get(
                                        "probe_feedback_history",
                                        [],
                                    )

                            def _load_feedback_history() -> object:
                                if not callable(load_runtime_state):
                                    return []
                                runtime_state = load_runtime_state()
                                if not isinstance(runtime_state, dict):
                                    return []
                                return runtime_state.get("probe_feedback_history", [])

                            if asyncio.iscoroutinefunction(tick_fn):
                                try:
                                    await tick_fn(
                                        profile,
                                        feedback_history=feedback_history,
                                        feedback_history_loader=_load_feedback_history,
                                    )
                                except TypeError:
                                    try:
                                        await tick_fn(
                                            profile,
                                            feedback_history=feedback_history,
                                        )
                                    except TypeError:
                                        await tick_fn(profile)
                            else:
                                try:
                                    tick_fn(
                                        profile,
                                        feedback_history=feedback_history,
                                        feedback_history_loader=_load_feedback_history,
                                    )
                                except TypeError:
                                    try:
                                        tick_fn(profile, feedback_history=feedback_history)
                                    except TypeError:
                                        tick_fn(profile)
                        except Exception:
                            logger.exception("Background force_tick after confirm failed")

                    asyncio.create_task(_bg_force_tick())
                # Record cognition update so it shows in "阿b最近记住了什么"
                _record_probe_cognition(
                    f"你确认了对「{domain}」的兴趣，已加入画像。",
                    domain,
                    "confirmed",
                )
                # Notify frontend via WebSocket
                await _publish_probe_event(
                    "interest.confirmed",
                    f"你确认了对「{domain}」的兴趣，已加入画像。",
                    domain,
                )
            return {"ok": ok, "action": "confirmed", "domain": domain}

        if response_type == "reject":
            metadata = _probe_metadata_from_active_speculation(speculator, domain)
            ok = speculator.user_reject_speculation(domain)
            if ok:
                _record_probe_feedback_history(
                    domain,
                    "reject",
                    speculator=speculator,
                    metadata=metadata,
                )
                _record_probe_cognition(
                    f"你对「{domain}」暂时不感兴趣，30 天内不再推送。",
                    domain,
                    "rejected",
                )
                await _publish_probe_event(
                    "interest.rejected",
                    f"已记录：你对「{domain}」暂时不感兴趣，30 天内不再推送。",
                    domain,
                )
            return {"ok": ok, "action": "rejected", "domain": domain}

        if response_type == "defer":
            metadata = _probe_metadata_from_active_speculation(speculator, domain)
            defer_result = speculator.user_defer_speculation(domain)
            ok = defer_result.outcome in {"deferred", "exhausted"}
            exhausted = defer_result.outcome == "exhausted"
            if ok:
                _record_probe_feedback_history(
                    domain,
                    "defer_exhausted" if exhausted else "defer",
                    speculator=speculator,
                    classification="defer",
                    classifier="user_button",
                    resulting_action="defer_exhausted" if exhausted else "deferred",
                    metadata={
                        **metadata,
                        "defer_count": defer_result.defer_count,
                        "deferred_until": defer_result.deferred_until,
                    },
                )
                if exhausted:
                    _record_probe_cognition(
                        f"「{domain}」已被多次搁置，之后不再主动提。",
                        domain,
                        "rejected",
                    )
                    await _publish_probe_event(
                        "interest.rejected",
                        f"「{domain}」已被多次搁置，暂不再提。",
                        domain,
                    )
                else:
                    _record_probe_cognition(
                        f"你把「{domain}」先放一放，过阵子再提。",
                        domain,
                        "deferred",
                    )
                    await _publish_probe_event(
                        "interest.deferred",
                        f"「{domain}」先放一放，过阵子可能再提。",
                        domain,
                    )
            return {
                "ok": ok,
                "action": "defer_exhausted" if exhausted else "deferred",
                "domain": domain,
                "deferred_until": defer_result.deferred_until,
                "defer_count": defer_result.defer_count,
            }

        # Chat: forward to dialogue with domain context injected
        raw_message = str(payload.get("message", "")).strip()
        if not raw_message:
            raw_message = f"我想聊聊你猜我可能感兴趣的「{domain}」这个方向"
        # Inject domain context so dialogue engine + learn_from_dialogue
        # understand this is feedback on a specific speculated interest
        contextual_message = f"[关于猜测兴趣「{domain}」的反馈] {raw_message}"

        async def _run_probe_chat(dialogue_owner: Any) -> JSONResponse:
            # Resolve every swappable owner only after admission. A request
            # queued behind hot reload must pair the new dialogue with the
            # new soul runtime for all post-reply mutations.
            current_speculator = getattr(ctx.soul_engine, "_speculator", None)
            if current_speculator is None:
                raise RuntimeError("Speculator not available")
            reply = await asyncio.wait_for(
                dialogue_owner.respond(contextual_message),
                timeout=30,
            )
            sentiment, classifier = await _classify_probe_sentiment(
                raw_message,
                reply,
                domain,
            )

            chat_response = "chat_neutral"
            resulting_action = "none"
            if sentiment == "negative":
                chat_response = "chat_rejected"
                resulting_action = "rejected"
                current_speculator.user_reject_speculation(domain, cooldown_days=14)
                summary = f"你对「{domain}」的反馈偏负面（{raw_message}），已暂时搁置 14 天。"
            elif sentiment == "strong_positive":
                chat_response = "chat_confirmed"
                resulting_action = "confirmed"
                _confirm_speculation_with_source(
                    current_speculator,
                    domain,
                    confirmation_source="chat_confirmed",
                )
                summary = f"你明确确认了对「{domain}」的兴趣，已加入画像。"
            elif sentiment == "weak_positive":
                chat_response = "weak_positive"
                resulting_action = "weak_positive_deferred"
                _record_exploration_buffer_event(
                    domain=domain,
                    source_event="weak_positive_chat",
                )
                summary = f"你对「{domain}」有轻微信号，先作为短期探索方向观察。"
            elif sentiment == "neutral_deferred":
                defer_result = current_speculator.user_defer_speculation(domain)
                if defer_result.outcome == "exhausted":
                    chat_response = "defer_exhausted"
                    resulting_action = "defer_exhausted"
                    summary = f"你多次想把「{domain}」放一放，之后先不提了。"
                else:
                    chat_response = "defer"
                    resulting_action = "deferred"
                    summary = f"你想把「{domain}」先放一放，过阵子再聊。"
            else:
                summary = f"关于「{domain}」你说：{raw_message}"

            _record_probe_feedback_history(
                domain,
                chat_response,
                speculator=current_speculator,
                message=raw_message,
                classification=sentiment,
                classifier=classifier,
                resulting_action=resulting_action,
            )
            detail = f"你的反馈：{raw_message}\n阿b的回复：{reply}"
            _record_probe_cognition(summary, domain, "chat", detail=detail)
            await _publish_probe_event("interest.chat", summary, domain)
            return JSONResponse(
                content={"ok": True, "action": "chat", "domain": domain, "reply": reply}
            )

        try:
            return cast("JSONResponse", await _run_with_dialogue_execution(_run_probe_chat))
        except Exception as exc:
            logger.exception("Dialogue failed for probe chat: %s", domain)
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": safe_llm_failure_message(exc),
            }

    @app.post("/api/avoidance-probes/trigger")
    async def trigger_avoidance_probe() -> dict[str, Any]:
        """Manually trigger an avoidance probe push via WebSocket."""
        controller = ctx.runtime_controller
        if controller is None:
            raise HTTPException(status_code=503, detail="Runtime controller not available")
        publish = getattr(controller, "_publish_avoidance_probe_if_available", None)
        if not callable(publish):
            raise HTTPException(status_code=503, detail="Avoidance probe publisher not available")
        await publish()
        return {"ok": True, "action": "avoidance_probe_triggered"}

    @app.get("/api/avoidance-probes/pending")
    async def pending_avoidance_probes() -> dict[str, Any]:
        """Return active speculative avoidances awaiting user response."""
        try:
            from openbiliclaw.soul.avoidance_speculator import load_avoidance_state

            runtime_config = getattr(ctx, "config", None) or config
            avoidance_state = load_avoidance_state(runtime_config.data_path)
            active = [item for item in avoidance_state.active if item.status == "active"]
            items = [
                {
                    "domain": item.domain,
                    "reason": item.reason,
                    "confidence": item.confidence,
                    "source_mode": item.source_mode,
                    "source_signal": item.source_signal,
                    "status": item.status,
                    "specifics": [
                        {"name": specific.name, "confirmation_count": specific.confirmation_count}
                        for specific in item.specifics
                        if specific.name.strip()
                    ],
                }
                for item in active[:6]
            ]
            return {"items": items}
        except Exception:
            logger.debug("Failed to load pending avoidance probes", exc_info=True)
            return {"items": []}

    @app.post("/api/avoidance-probes/respond")
    async def respond_to_avoidance_probe(payload: dict[str, Any]) -> Any:
        """User responds to a speculated avoidance probe."""
        domain = str(payload.get("domain", "")).strip()
        response_type = str(payload.get("response", "")).strip().lower()

        if not domain:
            raise HTTPException(status_code=422, detail="domain is required")
        if response_type not in {"confirm", "reject", "defer", "chat"}:
            raise HTTPException(
                status_code=422, detail="response must be confirm, reject, defer, or chat"
            )

        speculator: Any = getattr(ctx.soul_engine, "_avoidance_speculator", None)
        if response_type != "chat" and speculator is None:
            raise HTTPException(status_code=503, detail="Avoidance speculator not available")

        def metadata_fn(item_domain: str) -> dict[str, object]:
            return _probe_metadata_from_active_avoidance(
                speculator,
                item_domain,
            )

        if response_type == "confirm":
            metadata = metadata_fn(domain)
            confirm_fn = getattr(speculator, "user_confirm_avoidance", None)
            active_avoidance = confirm_fn(domain) if callable(confirm_fn) else None
            ok = active_avoidance is not None
            if ok:
                _record_probe_feedback_history(
                    domain,
                    "confirm",
                    speculator=speculator,
                    state_key="avoidance_probe_feedback_history",
                    metadata=metadata,
                )
                topics = topics_for_confirmed_avoidance(active_avoidance)
                summary = f"你确认了避开「{domain}」，已开始更新不喜欢方向。"
                _record_probe_cognition(
                    summary,
                    domain,
                    "confirmed",
                    source="avoidance_probe",
                )
                await _publish_probe_event("avoidance.confirmed", summary, domain)

                async def _apply_confirmed_avoidance() -> None:
                    try:
                        changes = await apply_new_dislikes(
                            memory=ctx.memory_manager,
                            database=getattr(ctx, "database", None)
                            or getattr(ctx.memory_manager, "_database", None),
                            embedding_service=getattr(ctx.soul_engine, "_embedding_service", None),
                            llm_service=getattr(ctx, "llm_service", None),
                            topics=topics,
                        )
                        if changes:
                            _record_probe_cognition(
                                f"避雷方向「{domain}」的不喜欢画像已更新。",
                                domain,
                                "confirmed",
                                source="avoidance_probe",
                                detail="\n".join(changes),
                            )
                    except Exception:
                        logger.exception(
                            "Background avoidance dislike writeback failed: %s",
                            domain,
                        )

                task = asyncio.create_task(_apply_confirmed_avoidance())
                _fire_and_forget_tasks.add(task)
                task.add_done_callback(_fire_and_forget_tasks.discard)
            return {"ok": ok, "action": "confirmed", "domain": domain}

        if response_type == "reject":
            metadata = metadata_fn(domain)
            reject_fn = getattr(speculator, "user_reject_avoidance", None)
            ok = bool(reject_fn(domain) if callable(reject_fn) else False)
            if ok:
                _record_probe_feedback_history(
                    domain,
                    "reject",
                    speculator=speculator,
                    state_key="avoidance_probe_feedback_history",
                    metadata=metadata,
                )
                _record_probe_cognition(
                    f"你表示并不需要避开「{domain}」，30 天内不再推送。",
                    domain,
                    "rejected",
                    source="avoidance_probe",
                )
                await _publish_probe_event(
                    "avoidance.rejected",
                    f"已记录：你并不需要避开「{domain}」，30 天内不再推送。",
                    domain,
                )
            return {"ok": ok, "action": "rejected", "domain": domain}

        if response_type == "defer":
            metadata = metadata_fn(domain)
            defer_fn = getattr(speculator, "user_defer_avoidance", None)
            defer_result = defer_fn(domain) if callable(defer_fn) else None
            ok = defer_result is not None and defer_result.outcome in {"deferred", "exhausted"}
            exhausted = defer_result is not None and defer_result.outcome == "exhausted"
            if ok and defer_result is not None:
                _record_probe_feedback_history(
                    domain,
                    "defer_exhausted" if exhausted else "defer",
                    speculator=speculator,
                    classification="defer",
                    classifier="user_button",
                    resulting_action="defer_exhausted" if exhausted else "deferred",
                    state_key="avoidance_probe_feedback_history",
                    metadata={
                        **metadata,
                        "defer_count": defer_result.defer_count,
                        "deferred_until": defer_result.deferred_until,
                    },
                )
                if exhausted:
                    _record_probe_cognition(
                        f"避雷方向「{domain}」已被多次搁置，之后不再主动提。",
                        domain,
                        "rejected",
                        source="avoidance_probe",
                    )
                    await _publish_probe_event(
                        "avoidance.rejected",
                        f"避雷方向「{domain}」已被多次搁置，暂不再提。",
                        domain,
                    )
                else:
                    _record_probe_cognition(
                        f"你把避雷方向「{domain}」先放一放，过阵子再提。",
                        domain,
                        "deferred",
                        source="avoidance_probe",
                    )
                    await _publish_probe_event(
                        "avoidance.deferred",
                        f"避雷方向「{domain}」先放一放，过阵子可能再提。",
                        domain,
                    )
            return {
                "ok": ok,
                "action": "defer_exhausted" if exhausted else "deferred",
                "domain": domain,
                "deferred_until": defer_result.deferred_until if defer_result else "",
                "defer_count": defer_result.defer_count if defer_result else 0,
            }

        raw_message = str(payload.get("message", "")).strip()
        if not raw_message:
            raw_message = f"我想聊聊你猜我可能想避开的「{domain}」这个方向"
        contextual_message = f"[关于避雷方向「{domain}」的反馈] {raw_message}"

        async def _run_avoidance_chat(dialogue_owner: Any) -> JSONResponse:
            # Keep the dialogue and avoidance state owner from the same
            # post-reload RuntimeContext. The outer ``speculator`` is only for
            # synchronous button actions that never wait on this lease.
            current_speculator = getattr(ctx.soul_engine, "_avoidance_speculator", None)
            if current_speculator is None:
                raise RuntimeError("Avoidance speculator not available")

            def current_metadata_fn(item_domain: str) -> dict[str, object]:
                return _probe_metadata_from_active_avoidance(
                    current_speculator,
                    item_domain,
                )

            reply = await asyncio.wait_for(
                dialogue_owner.respond(contextual_message),
                timeout=30,
            )
            sentiment, classifier = await _classify_probe_sentiment(
                raw_message,
                reply,
                domain,
            )

            if sentiment == "negative":
                chat_response = "avoidance_chat_confirmed"
                resulting_action = "confirmed"
                current_speculator.observe(
                    [
                        {
                            "event_type": "dislike",
                            "title": domain,
                            "metadata": {
                                "feedback_type": "dislike",
                                "user_message": raw_message,
                                "source": "avoidance_probe_chat",
                            },
                        }
                    ]
                )
                summary = f"你确认「{domain}」偏向不喜欢，确认度 +1。"
            elif sentiment in {"strong_positive", "weak_positive"}:
                chat_response = "avoidance_chat_rejected"
                resulting_action = "rejected"
                reject_fn = getattr(current_speculator, "user_reject_avoidance", None)
                if callable(reject_fn):
                    reject_fn(domain, cooldown_days=14)
                summary = f"你表示其实不排斥「{domain}」，已暂时搁置 14 天。"
            elif sentiment == "neutral_deferred":
                defer_fn = getattr(current_speculator, "user_defer_avoidance", None)
                defer_result = defer_fn(domain) if callable(defer_fn) else None
                if defer_result is not None and defer_result.outcome == "exhausted":
                    chat_response = "defer_exhausted"
                    resulting_action = "defer_exhausted"
                    summary = f"你多次想把避雷方向「{domain}」放一放，之后先不提了。"
                else:
                    chat_response = "defer"
                    resulting_action = "deferred"
                    summary = f"你想把避雷方向「{domain}」先放一放，过阵子再聊。"
            else:
                chat_response = "avoidance_chat_neutral"
                resulting_action = "none"
                summary = f"关于避雷方向「{domain}」你说：{raw_message}"

            _record_probe_feedback_history(
                domain,
                chat_response,
                speculator=current_speculator,
                message=raw_message,
                classification=sentiment,
                classifier=classifier,
                resulting_action=resulting_action,
                state_key="avoidance_probe_feedback_history",
                metadata_fn=current_metadata_fn,
            )
            detail = f"你的反馈：{raw_message}\n阿b的回复：{reply}"
            _record_probe_cognition(
                summary,
                domain,
                "chat",
                source="avoidance_probe",
                detail=detail,
            )
            await _publish_probe_event("avoidance.chat", summary, domain)
            return JSONResponse(
                content={"ok": True, "action": "chat", "domain": domain, "reply": reply}
            )

        try:
            return cast("JSONResponse", await _run_with_dialogue_execution(_run_avoidance_chat))
        except Exception as exc:
            logger.exception("Dialogue failed for avoidance probe chat: %s", domain)
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": safe_llm_failure_message(exc),
            }

    @app.post("/api/feedback", response_model=FeedbackResponse)
    async def feedback(payload: FeedbackIn) -> FeedbackResponse:
        feedback_type = payload.feedback_type.strip().lower()
        note = payload.note.strip()
        if feedback_type not in {"like", "dislike", "comment", "dismiss"}:
            raise HTTPException(status_code=422, detail="Unsupported feedback type.")
        if feedback_type == "comment" and not note:
            raise HTTPException(status_code=422, detail="Comment feedback requires note.")

        recommendation = ctx.database.get_recommendation_by_id(payload.recommendation_id)
        if recommendation is None:
            raise HTTPException(status_code=404, detail="Recommendation not found.")

        from openbiliclaw.sources.event_format import (
            SOURCE_BILIBILI,
            build_event,
            format_event_context,
        )

        rec_title = str(recommendation.get("title", ""))
        source_platform = (
            str(recommendation.get("source_platform") or SOURCE_BILIBILI).strip().lower()
            or SOURCE_BILIBILI
        )
        feedback_context = format_event_context(
            event_type=feedback_type,
            source_platform=source_platform,
            title=rec_title,
        )
        if note:
            feedback_context = f"{feedback_context},备注:{note}"
        feedback_event = build_event(
            event_type="feedback",
            source_platform=source_platform,
            title=rec_title,
            context=feedback_context,
            metadata={
                "recommendation_id": payload.recommendation_id,
                "bvid": recommendation.get("bvid", ""),
                "feedback_type": feedback_type,
                "feedback_note": note,
                "event_namespace": "recommendation",
                "profile_update_owner": "content_feedback",
            },
        )
        feedback_event["ingest_key"] = payload.request_id.strip()
        ingress_receipt = await event_ingress.accept(
            feedback_event,
            producer="feedback",
        )
        if ingress_receipt.accepted != 1 or ingress_receipt.rejected:
            reason = ingress_receipt.items[0].error if ingress_receipt.items else "rejected"
            raise HTTPException(status_code=422, detail=f"feedback event rejected: {reason}")
        item_receipt = ingress_receipt.items[0]
        stored_feedback = _durable_ingress_row(
            ctx,
            event_id=item_receipt.event_id,
            inserted=item_receipt.inserted,
            submitted_event=feedback_event,
        )
        stored_metadata = _event_row_metadata(stored_feedback)
        stored_recommendation_id = int(stored_metadata.get("recommendation_id") or 0)
        stored_feedback_type = str(stored_metadata.get("feedback_type") or "").strip().lower()
        stored_note = str(stored_metadata.get("feedback_note") or "").strip()
        if (
            stored_recommendation_id != payload.recommendation_id
            or stored_feedback_type != feedback_type
            or stored_note != note
        ):
            raise HTTPException(
                status_code=409,
                detail="request_id was already used for different feedback",
            )
        # Durable projection: a duplicate retry repairs commit→projection
        # failures, while conflicting payloads above never drive new state.
        ctx.database.update_recommendation_feedback(
            stored_recommendation_id,
            feedback_type=stored_feedback_type,
            feedback_note=stored_note,
        )
        _invalidate_recommendation_snapshot()
        buffer_domain, buffer_specifics = _recommendation_buffer_domain(recommendation)
        if item_receipt.inserted and feedback_type == "like":
            _record_exploration_buffer_event(
                domain=buffer_domain,
                specifics=buffer_specifics,
                source_event="card_like",
                evidence_id=str(recommendation.get("bvid", "")),
            )
        elif item_receipt.inserted and feedback_type == "dislike":
            _record_exploration_buffer_event(
                domain=buffer_domain,
                specifics=buffer_specifics,
                source_event="negative",
                evidence_id=str(recommendation.get("bvid", "")),
            )
        record_immediate_feedback_cognition = getattr(
            ctx.soul_engine,
            "record_immediate_feedback_cognition",
            None,
        )
        if item_receipt.inserted and callable(record_immediate_feedback_cognition):
            with suppress(Exception):
                record_immediate_feedback_cognition(
                    feedback_type=feedback_type,
                    title=str(recommendation.get("title", "")),
                    note=note,
                )
        return FeedbackResponse(
            ok=True,
            recommendation_id=payload.recommendation_id,
            feedback_type=feedback_type,
            event_id=item_receipt.event_id,
            duplicate=item_receipt.duplicate,
            processing="queued",
        )

    @app.post(
        "/api/recommendation-click",
        response_model=RecommendationClickResponse,
    )
    async def recommendation_click(
        payload: RecommendationClickIn,
    ) -> RecommendationClickResponse:
        """Persist a recommendation click-through for async strong-signal processing.

        The click is evidence that the user actively chose a recommendation.
        This request commits that fact to the append-only event ledger and
        returns; the generic event owner projects it asynchronously. If the
        recommendation_id resolves to a stored card, its canonical metadata
        fills fields omitted by the client.
        """
        recommendation: dict[str, object] | None = None
        if payload.recommendation_id is not None:
            recommendation = ctx.database.get_recommendation_by_id(
                payload.recommendation_id,
            )

        bvid = (payload.bvid or "").strip()
        content_id = (payload.content_id or "").strip()
        content_url = (payload.content_url or "").strip()
        source_platform_raw = (payload.source_platform or "").strip()
        title = (payload.title or "").strip()
        topic_label = (payload.topic_label or "").strip()
        up_name = (payload.up_name or "").strip()

        if recommendation is not None:
            bvid = bvid or str(recommendation.get("bvid", "") or "").strip()
            content_id = content_id or str(recommendation.get("content_id", "") or "").strip()
            content_url = content_url or str(recommendation.get("content_url", "") or "").strip()
            source_platform_raw = (
                source_platform_raw or str(recommendation.get("source_platform", "") or "").strip()
            )
            title = title or str(recommendation.get("title", "") or "").strip()
            topic_label = topic_label or str(recommendation.get("topic_label", "") or "").strip()
            up_name = up_name or str(recommendation.get("up_name", "") or "").strip()

        content_id = content_id or bvid
        bvid = bvid or content_id
        if not bvid:
            raise HTTPException(status_code=422, detail="bvid is required.")
        if not source_platform_raw:
            source_platform_raw = _infer_source_platform_from_url(content_url)
        source_platform = _normalize_source_platform(source_platform_raw)
        if not content_url:
            content_url = _fallback_recommendation_click_url(
                source_platform=source_platform,
                content_id=content_id,
                bvid=bvid,
            )

        # Persist the click as an event so history/query paths can see it.
        from openbiliclaw.sources.event_format import (
            build_event,
            format_event_context,
        )

        click_extra_parts: list[str] = []
        if topic_label:
            click_extra_parts.append(f"主题:{topic_label}")
        click_context = format_event_context(
            event_type="click",
            source_platform=source_platform,
            title=title,
            author=up_name,
            extra=",".join(click_extra_parts),
        )
        click_metadata: dict[str, object] = {
            "recommendation_id": payload.recommendation_id,
            "bvid": bvid,
            "content_id": content_id,
            "content_url": content_url,
            "source_platform": source_platform,
            "topic_label": topic_label,
            "up_name": up_name,
            "source": "recommendation_click",
            "event_namespace": "recommendation",
            "profile_update_owner": "generic",
        }
        # v0.3.x event-satisfaction: forward dwell so the persisted
        # click row can be classified as meaningful_dwell vs quick_exit.
        # Absent fields stay absent; storage classifier degrades to
        # unknown / missing_dwell. Storage is the single classification
        # owner — do not classify here.
        if payload.watch_seconds is not None:
            click_metadata["watch_seconds"] = payload.watch_seconds
        if payload.video_duration_seconds is not None:
            click_metadata["video_duration_seconds"] = payload.video_duration_seconds
        click_event = build_event(
            event_type="click",
            source_platform=source_platform,
            title=title,
            url=content_url,
            author=up_name,
            context=click_context,
            metadata=click_metadata,
        )
        click_event["ingest_key"] = payload.request_id.strip()
        ingress_receipt = await event_ingress.accept(
            click_event,
            producer="recommendation",
        )
        if ingress_receipt.accepted != 1 or ingress_receipt.rejected:
            reason = ingress_receipt.items[0].error if ingress_receipt.items else "rejected"
            raise HTTPException(status_code=422, detail=f"click event rejected: {reason}")
        item_receipt = ingress_receipt.items[0]
        stored_click = _durable_ingress_row(
            ctx,
            event_id=item_receipt.event_id,
            inserted=item_receipt.inserted,
            submitted_event=click_event,
        )
        stored_metadata = _event_row_metadata(stored_click)
        stored_recommendation_id_raw = stored_metadata.get("recommendation_id")
        try:
            stored_recommendation_id = (
                int(stored_recommendation_id_raw)
                if stored_recommendation_id_raw is not None
                else None
            )
        except (TypeError, ValueError):
            stored_recommendation_id = None
        stored_content_id = str(
            stored_metadata.get("content_id") or stored_metadata.get("bvid") or ""
        ).strip()
        stored_content_url = str(
            stored_metadata.get("content_url") or stored_click.get("url") or ""
        ).strip()
        stored_source_platform = _normalize_source_platform(
            str(stored_metadata.get("source_platform") or "")
        )
        stable_content_identity = bool(content_id or bvid)
        identity_conflict = (
            stored_recommendation_id != payload.recommendation_id
            or stored_content_id != content_id
            or stored_source_platform != source_platform
        )
        if not stable_content_identity:
            identity_conflict = identity_conflict or (
                _normalize_recommendation_click_identity_url(stored_content_url)
                != _normalize_recommendation_click_identity_url(content_url)
            )
        if identity_conflict:
            raise HTTPException(
                status_code=409,
                detail="request_id was already used for a different recommendation click",
            )
        stored_bvid = str(stored_metadata.get("bvid") or stored_content_id).strip()
        if item_receipt.inserted:
            buffer_domain, buffer_specifics = _recommendation_buffer_domain(
                {
                    "title": title,
                    "topic_label": topic_label,
                    "bvid": bvid,
                }
            )
            _record_exploration_buffer_event(
                domain=buffer_domain,
                specifics=buffer_specifics,
                source_event="plain_click",
                evidence_id=bvid,
            )

        return RecommendationClickResponse(
            ok=True,
            bvid=stored_bvid,
            layers_updated=[],
            event_id=item_receipt.event_id,
            duplicate=item_receipt.duplicate,
            processing="queued",
        )

    @app.post("/api/insights/feedback", response_model=InsightFeedbackResponse)
    async def insight_feedback(
        payload: InsightFeedbackIn,
        response: Response,
    ) -> InsightFeedbackResponse:
        """Deprecated compatibility forwarder for insight feedback.

        New clients act on durable dialogue cards. Old clients retain the same
        response shape while entering the identical serialized settlement path.
        """
        signal = payload.signal.strip().lower()
        if signal not in {"confirm", "like", "support", "reject", "dislike", "deny"}:
            raise HTTPException(status_code=422, detail="Unsupported insight feedback signal.")
        hypothesis = payload.hypothesis.strip()
        if not hypothesis:
            raise HTTPException(status_code=422, detail="hypothesis is required.")
        if ctx.soul_engine is None:
            raise HTTPException(status_code=503, detail="Soul engine not ready.")
        from openbiliclaw.soul.identity import build_hash8_map, insight_hash8

        ref = insight_hash8(hypothesis)
        load_insights = getattr(ctx.soul_engine, "_load_insights", None)
        if callable(load_insights):
            texts = [
                str(getattr(item, "hypothesis", "")).strip()
                for item in load_insights()
                if str(getattr(item, "hypothesis", "")).strip()
            ]
            mapping = build_hash8_map(texts)
            matched_ref = next(
                (candidate_ref for candidate_ref, text in mapping.items() if text == hypothesis),
                "",
            )
            if matched_ref:
                ref = matched_ref
        action = "confirm" if signal in {"confirm", "like", "support"} else "reject"
        result = await _settle_hypothesis(
            ref=ref,
            hypothesis=hypothesis,
            requested_verdict=action,
            turn_id=f"legacy-{uuid.uuid4().hex}",
            source="legacy_endpoint",
        )
        deprecation_headers = {
            "Deprecation": "true",
            "Link": '</api/chat/pending-confirmations>; rel="successor-version"',
        }
        for header_name, header_value in deprecation_headers.items():
            response.headers[header_name] = header_value
        outcome = str(result.get("outcome", "")).strip().lower()
        # Anchor refusal is a real failure: nothing was written to
        # card_settlements / profile_update_ledger. Never report ok=true
        # (the previous silent-success path broke old clients that only
        # checked the HTTP status / ok flag).
        if outcome in {"stale_anchor", "anchor_dependency_failed"}:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot settle this insight while another dialogue anchor "
                    f"is active (outcome={outcome}). Finish or release the "
                    "active conversation first, then retry."
                ),
                headers=deprecation_headers,
            )
        if outcome == "processing":
            response.status_code = 202
        return InsightFeedbackResponse(
            ok=outcome != "processing",
            matched=bool(result.get("matched", False)),
            hypothesis=str(result.get("hypothesis", hypothesis)),
            signal=str(result.get("signal", action)),
            validated=bool(result.get("validated", False)),
            confidence=float(result.get("confidence", 0.0)),
        )

    # ── Source recipe management endpoints ──────────────────────────

    @app.get("/api/sources")
    def list_sources() -> dict[str, Any]:
        """Return all source recipes."""
        recipes = ctx.database.get_all_recipes()
        return {"items": recipes}

    @app.post("/api/sources", status_code=201)
    def create_source(payload: dict[str, Any]) -> dict[str, Any]:
        """Create a new source recipe."""
        import uuid

        recipe_id = payload.get("id") or str(uuid.uuid4())
        source_type = payload.get("source_type", "")
        name = payload.get("name", "")
        strategy = payload.get("strategy", "")
        if not source_type or not name or not strategy:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=422,
                detail="source_type, name, and strategy are required",
            )
        recipe = {
            "id": recipe_id,
            "source_type": source_type,
            "name": name,
            "strategy": strategy,
            "config": payload.get("config", {}),
            "target_share": payload.get("target_share", 4),
            "enabled": payload.get("enabled", True),
            "created_by": payload.get("created_by", "user"),
        }
        ctx.database.save_source_recipe(recipe)
        return {"ok": True, "recipe": recipe}

    @app.put("/api/sources/{recipe_id}")
    def update_source(recipe_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update fields of an existing source recipe."""
        updated = ctx.database.update_recipe(recipe_id, **payload)
        if not updated:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Recipe not found")
        return {"ok": True, "id": recipe_id}

    @app.delete("/api/sources/{recipe_id}")
    def delete_source(recipe_id: str) -> dict[str, Any]:
        """Delete a source recipe (system recipes cannot be deleted)."""
        # Check if it's a system recipe
        all_recipes = ctx.database.get_all_recipes()
        target = next((r for r in all_recipes if r["id"] == recipe_id), None)
        if target and target.get("created_by") == "system":
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="System recipes cannot be deleted")
        deleted = ctx.database.delete_recipe(recipe_id)
        if not deleted:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Recipe not found")
        return {"ok": True, "id": recipe_id}

    # ── XHS observed URL ingestion endpoint ─────────────────────────

    xhs_max_urls_per_batch = 50
    xhs_url_prefix = "https://www.xiaohongshu.com/"

    def _discovery_candidate_pending_cap() -> int:
        from openbiliclaw.discovery.candidate_pool import discovery_candidate_pending_cap

        scheduler = getattr(config, "scheduler", None)
        target = int(getattr(scheduler, "pool_target_count", 300) or 300)
        return discovery_candidate_pending_cap(target)

    def _intish(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _cache_bili_search_videos(
        database: Any,
        videos: list[dict[str, Any]],
        *,
        query: str = "",
        source_keyword_id: int | None = None,
        discovery_lane: str = "",
    ) -> int:
        """Enqueue extension-collected Bilibili search videos for evaluation."""

        from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
        from openbiliclaw.discovery.engine import DiscoveredContent
        from openbiliclaw.published_time import normalize_published_time

        enqueue = getattr(database, "enqueue_discovery_candidates", None)
        if not callable(enqueue):
            return 0
        writes = []
        lane = "recent" if str(discovery_lane).strip().lower() == "recent" else ""
        source_context = "bili-extension-search:recent" if lane else "bili-extension-search"
        for video in videos:
            bvid = str(video.get("bvid") or video.get("content_id") or "").strip()
            if not bvid:
                continue
            title = str(video.get("title") or "").strip()
            if not title:
                continue
            up_name = str(
                video.get("up_name") or video.get("author_name") or video.get("author") or ""
            ).strip()
            content_url = str(video.get("content_url") or video.get("url") or "").strip()
            if not content_url:
                content_url = f"https://www.bilibili.com/video/{bvid}"
            tags_raw = video.get("tags")
            tags = (
                [str(item).strip() for item in tags_raw if str(item).strip()]
                if isinstance(tags_raw, list)
                else []
            )
            published = normalize_published_time(
                video.get("published_at") or video.get("pubdate"),
                label=video.get("published_label"),
            )
            item = DiscoveredContent(
                bvid=bvid,
                title=title,
                up_name=up_name,
                up_mid=_intish(video.get("up_mid") or video.get("mid")),
                cover_url=str(video.get("cover_url") or video.get("pic") or "").strip(),
                duration=_intish(video.get("duration")),
                view_count=_intish(video.get("view_count") or video.get("play")),
                like_count=_intish(video.get("like_count") or video.get("likes")),
                favorite_count=_intish(
                    video.get("favorite_count") or video.get("favorites") or video.get("favorite")
                ),
                danmaku_count=_intish(
                    video.get("danmaku_count") or video.get("danmaku") or video.get("video_review")
                ),
                comment_count=_intish(
                    video.get("comment_count") or video.get("reply") or video.get("review")
                ),
                share_count=_intish(video.get("share_count") or video.get("share")),
                tags=tags,
                description=str(video.get("description") or video.get("desc") or "").strip(),
                source_strategy="bili-extension-search",
                discovery_lane=lane,
                content_id=bvid,
                content_url=content_url,
                source_platform="bilibili",
                author_name=up_name,
                score_threshold=0.60,
                source_keyword_id=source_keyword_id,
                published_at=published.published_at,
                published_label=published.published_label,
            )
            writes.append(
                discovered_content_to_candidate_write(
                    item,
                    source_context=source_context,
                    raw_payload={
                        "bvid": bvid,
                        "query": query,
                        "url": content_url,
                        "admission_policy": "observed",
                        "score_threshold": 0.60,
                        **({"discovery_lane": lane} if lane else {}),
                    },
                )
            )
        if not writes:
            return 0
        try:
            return int(enqueue(writes, max_pending_per_source=_discovery_candidate_pending_cap()))
        except TypeError:
            return int(enqueue(writes))

    def _mark_bili_task_keyword_terminal(payload_json: str | None, *, success: bool) -> None:
        from openbiliclaw.sources.bili_tasks import source_keyword_id_from_bili_task

        keyword_id = source_keyword_id_from_bili_task(payload_json)
        if keyword_id is None:
            return
        method = "mark_keyword_used" if success else "mark_keyword_failed"
        mark = getattr(ctx.database, method, None)
        if not callable(mark):
            return
        with suppress(Exception):
            mark(keyword_id)

    def _pick_best_xhs_url(database: Any, note_id: str, incoming: str) -> str:
        """Return the most share-worthy URL for a xhs note.

        xhs search-result pages don't render ``xsec_token`` into ``<a href>``
        (React SPA keeps the token in props, not DOM), but explore-feed
        cards do. When the same note arrives both ways, prefer the URL
        that carries a token — without it, outbound links can silently
        dead-end at an xhs login wall.

        Order of preference:
        1. ``incoming`` URL if it already has ``xsec_token=``
        2. Any prior ``xhs_observed_urls`` row for this note with a token
        3. Existing ``content_cache.content_url`` if it has a token
        4. Fall back to ``incoming`` (bare URL — still works for the
           logged-in user on the xhs domain, just not guaranteed for
           share/outbound traffic)
        """
        if "xsec_token=" in incoming:
            return incoming
        try:
            row = database.conn.execute(
                "SELECT url FROM xhs_observed_urls "
                "WHERE url LIKE ? AND url LIKE '%xsec_token=%' "
                "ORDER BY observed_at DESC LIMIT 1",
                (f"%/{note_id}?%",),
            ).fetchone()
            if row and row["url"]:
                return str(row["url"])
        except Exception:
            pass
        try:
            row = database.conn.execute(
                "SELECT content_url FROM content_cache WHERE bvid=?",
                (note_id,),
            ).fetchone()
            if row and isinstance(row["content_url"], str) and "xsec_token=" in row["content_url"]:
                return str(row["content_url"])
        except Exception:
            pass
        try:
            row = database.conn.execute(
                "SELECT content_url FROM discovery_candidates "
                "WHERE source_platform='xiaohongshu' AND content_id=? "
                "  AND content_url LIKE '%xsec_token=%' "
                "ORDER BY last_seen_at DESC LIMIT 1",
                (note_id,),
            ).fetchone()
            if row and row["content_url"]:
                return str(row["content_url"])
        except Exception:
            pass
        return incoming

    def _backfill_xhs_tokens(database: Any, urls: list[str]) -> int:
        """Upgrade cached xhs rows whose content_url lacks xsec_token.

        The extension often observes the same note twice — once from a
        search result page (no token in ``<a href>``) and once from an
        explore-feed card (token present). When a tokenized URL arrives
        later, rewrite the previously-cached bare URL so share links
        don't dead-end at xhs's login wall.
        """
        from urllib.parse import urlparse

        try:
            connection = database.conn
        except Exception:
            return 0

        def best_effort_update(sql: str, params: tuple[str, str]) -> int:
            """Run one optional projection without leaving a zero-row transaction open."""
            try:
                cursor = connection.execute(sql, params)
                # SQLite starts an implicit write transaction even when UPDATE
                # matches zero rows.  Commit every successful statement, not
                # only statements whose ``rowcount`` is positive; otherwise a
                # thread-affine request connection can retain the writer lock
                # after TestClient/request teardown indefinitely.
                connection.commit()
                return int(cursor.rowcount or 0)
            except Exception:
                with suppress(Exception):
                    connection.rollback()
                return 0

        updated = 0
        for url in urls:
            if "xsec_token=" not in url:
                continue
            try:
                path = urlparse(url).path.strip("/")
                note_id = path.rsplit("/", 1)[-1] if path else ""
            except Exception:
                continue
            if not note_id:
                continue
            updated += best_effort_update(
                (
                    "UPDATE content_cache SET content_url=? "
                    "WHERE bvid=? AND source_platform='xiaohongshu' "
                    "AND (content_url = '' OR content_url NOT LIKE '%xsec_token=%')"
                ),
                (url, note_id),
            )
            updated += best_effort_update(
                (
                    "UPDATE discovery_candidates "
                    "SET content_url=?, last_seen_at=CURRENT_TIMESTAMP "
                    "WHERE source_platform='xiaohongshu' AND content_id=? "
                    "AND (content_url = '' OR content_url NOT LIKE '%xsec_token=%')"
                ),
                (url, note_id),
            )
        return updated

    # ── XHS self-author filter (v0.3.48+) ────────────────────────────
    #
    # XHS search / explore / saved-author paths all happily return the
    # logged-in user's own published notes. Without filtering, the
    # recommendation pool fills with content the user posted themselves
    # ("自己发的笔记被推回给自己" — observed in 2026-05-05 logs as
    # 屎屎/三花/etc. cat photos polluting the popup). The extension
    # bootstrap captures self user_id + nickname from XHS state and
    # sends it back via ``debug.xhs_bootstrap.steps[*].self_info``.
    # Backend persists in ``discovery_runtime_state["xhs_self_info"]``
    # and consults it on every ingest path.

    def _normalize_self_info(raw: Any) -> dict[str, str] | None:
        """Validate + normalize a self_info-shaped dict.

        Returns ``{"user_id": ..., "nickname": ...}`` if either field is
        non-empty, otherwise ``None``.
        """
        if not isinstance(raw, dict):
            return None
        user_id = str(raw.get("user_id", "") or "").strip()
        nickname = str(raw.get("nickname", "") or "").strip()
        if not user_id and not nickname:
            return None
        return {"user_id": user_id, "nickname": nickname}

    def _extract_self_info_from_payload(payload: Any) -> dict[str, str] | None:
        """Pull self_info from any XHS ingest payload.

        v0.3.57+: extension v0.3.10 sends self_info at the **payload top
        level** for every ingest path (passive ``observed-urls``, search /
        creator ``task-result``, bootstrap_profile ``task-result``). The
        legacy bootstrap-only nested location
        ``debug.xhs_bootstrap.steps[*].self_info`` (v0.3.48 / extension
        v0.3.9) is kept as fallback for older extensions.
        """
        if not isinstance(payload, dict):
            return None
        # 1) New top-level location.
        info = _normalize_self_info(payload.get("self_info"))
        if info is not None:
            return info
        # 2) Legacy bootstrap-debug nested location.
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            return None
        bootstrap = debug.get("xhs_bootstrap")
        if not isinstance(bootstrap, dict):
            return None
        steps = bootstrap.get("steps")
        if not isinstance(steps, list):
            return None
        for step in steps:
            if not isinstance(step, dict):
                continue
            info = _normalize_self_info(step.get("self_info"))
            if info is not None:
                return info
        return None

    def _persist_xhs_self_info(self_info: dict[str, str]) -> None:
        """Save self info into discovery_runtime_state if not already there."""
        memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        if memory_manager is None:
            return
        try:
            state = memory_manager.load_discovery_runtime_state()
            existing = state.get("xhs_self_info")
            # Idempotent: only write when content changes (avoid sqlite churn).
            if isinstance(existing, dict) and existing == self_info:
                return
            update_state = getattr(memory_manager, "update_discovery_runtime_state", None)
            if callable(update_state):
                update_state(
                    lambda runtime_state: runtime_state.update({"xhs_self_info": self_info})
                )
            else:
                state["xhs_self_info"] = self_info
                memory_manager.save_discovery_runtime_state(state)
            logger.info(
                "xhs self_info persisted: user_id=%s nickname=%r",
                self_info.get("user_id", ""),
                self_info.get("nickname", ""),
            )
            # Immediately purge any self-authored rows that slipped into
            # the pool before this self_info was known.
            suppressed = _purge_self_authored_pool_items(ctx.database, self_info)
            if suppressed:
                logger.info(
                    "xhs self_info purge: suppressed %d self-authored pool item(s) (nickname=%r)",
                    suppressed,
                    self_info.get("nickname", ""),
                )
        except Exception:
            logger.exception("Failed to persist xhs self_info")

    def _load_xhs_self_info() -> dict[str, str]:
        """Load self info from runtime state (returns empty dict on miss)."""
        memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        if memory_manager is None:
            return {}
        try:
            state = memory_manager.load_discovery_runtime_state()
            existing = state.get("xhs_self_info")
            if isinstance(existing, dict):
                return {
                    "user_id": str(existing.get("user_id", "") or ""),
                    "nickname": str(existing.get("nickname", "") or ""),
                }
        except Exception:
            logger.exception("Failed to load xhs self_info")
        return {}

    def _is_self_authored_note(note: dict[str, Any], self_info: dict[str, str]) -> bool:
        """Check whether a note's author matches the logged-in user.

        Both user_id and nickname can match — XHS sometimes only ships
        nickname in note metadata (no author user_id), other times both.
        Treat the match as case-insensitive on the trimmed values.
        """
        if not self_info:
            return False
        nickname = self_info.get("nickname", "").strip().lower()
        user_id = self_info.get("user_id", "").strip().lower()
        author = str(note.get("author", "") or "").strip().lower()
        if author and nickname and author == nickname:
            return True
        author_id = str(note.get("author_id", "") or "").strip().lower()
        return bool(author_id and user_id and author_id == user_id)

    def _purge_self_authored_pool_items(
        database: Any,
        self_info: dict[str, str],
    ) -> int:
        """Mark every pool row authored by ``self_info.nickname`` as suppressed.

        v0.3.57+: cleans up content_cache rows that entered before the
        per-path self_info filter was wired in. Idempotent — already-
        suppressed rows are not flipped further. Returns the number of
        rows actually changed in this call.

        ``up_name`` is the column populated by ``_cache_xhs_notes`` from
        the note's ``author`` field, so the comparison mirrors the
        runtime filter exactly.
        """
        if not self_info or not hasattr(database, "conn"):
            return 0
        nickname = (self_info.get("nickname") or "").strip()
        if not nickname:
            return 0
        connection = database.conn
        try:
            cursor = connection.execute(
                "UPDATE content_cache "
                "SET pool_status = 'suppressed' "
                "WHERE source_platform = 'xiaohongshu' "
                "  AND COALESCE(pool_status, 'fresh') = 'fresh' "
                "  AND ("
                "    LOWER(COALESCE(up_name, '')) = LOWER(?)"
                "    OR LOWER(COALESCE(author_name, '')) = LOWER(?)"
                "  )",
                (nickname, nickname),
            )
            connection.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            with suppress(Exception):
                connection.rollback()
            logger.exception("Failed to purge self-authored xhs pool items")
            return 0

    def _cache_xhs_notes(
        database: Any,
        notes: list[dict[str, Any]],
        page_type: str,
        self_info: dict[str, str] | None = None,
        *,
        source_keyword_id: int | None = None,
    ) -> int:
        """Enqueue xhs note metadata from the extension into discovery_candidates.

        ``self_info`` (v0.3.48+) lets the caller pass the just-extracted
        login fingerprint from the same request — avoids a round-trip
        through ``discovery_runtime_state`` and works against test
        stubs that haven't implemented the runtime-state API.  When
        ``None``, falls back to the persisted state.

        ``source_keyword_id`` (P1.8) is the ``discovery_keywords.id`` carried on
        the originating xhs *search* task payload. XHS is truly async, so the id
        cannot be stamped at search time — it rides the task and is threaded onto
        each ingested candidate here so admission can backfill the keyword's
        yield. ``None`` for passive / observed / non-planner ingests.
        """
        from urllib.parse import urlparse

        from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
        from openbiliclaw.discovery.engine import DiscoveredContent
        from openbiliclaw.published_time import normalize_published_time

        enqueue = getattr(database, "enqueue_discovery_candidates", None)
        if not callable(enqueue):
            return 0
        if self_info is None:
            self_info = _load_xhs_self_info()
        writes = []
        skipped_self = 0
        covers_saved = 0
        for note in notes:
            if _is_self_authored_note(note, self_info):
                skipped_self += 1
                continue
            url = note.get("url", "")
            if not isinstance(url, str) or not url.startswith(xhs_url_prefix):
                continue
            # Extract note ID from URL path
            try:
                path = urlparse(url).path.strip("/")
                note_id = path.rsplit("/", 1)[-1] if path else ""
            except Exception:
                note_id = ""
            if not note_id:
                continue

            title = str(note.get("title", "") or "").strip()
            if not title:
                continue  # Skip notes with empty title — they produce blank recommendation cards
            author = str(note.get("author", "") or "").strip()
            cover_url = str(note.get("cover_url", "") or "").strip()
            if cover_url.startswith("//"):
                cover_url = f"https:{cover_url}"
            if not cover_url.startswith(("http://", "https://")):
                # Lazy-load data:/blob: placeholders from background-tab
                # scrapes are not covers; storing them yields cards that can
                # never render (the image proxy rejects non-http(s) URLs).
                cover_url = ""
            # Extension-harvested cover bytes: xhs card scrapes attach
            # cover_data so the cover lands in the disk cache at ingest time
            # (while the rotating xhscdn token is fresh) instead of depending
            # on the backend's later fetch succeeding from this machine.
            # Saved even when the note later dedupes as a known candidate —
            # that heals stock rows whose cover was never cached.
            # Best-effort: a bad cover never blocks the note.
            cover_data = note.get("cover_data")
            if (
                cover_url
                and isinstance(cover_data, str)
                and cover_data
                and save_extension_cover(
                    cover_url,
                    cover_data,
                    str(note.get("cover_content_type", "") or ""),
                )
            ):
                covers_saved += 1
            best_url = _pick_best_xhs_url(database, note_id, url)
            published = normalize_published_time(
                note.get("published_at") or note.get("pubdate"),
                label=note.get("published_label"),
            )

            item = DiscoveredContent(
                bvid=note_id,
                title=title,
                up_name=author,
                cover_url=cover_url,
                view_count=_intish(note.get("view_count") or note.get("views")),
                like_count=_intish(note.get("like_count") or note.get("likes")),
                collect_count=_intish(
                    note.get("collect_count")
                    or note.get("favorite_count")
                    or note.get("favorites")
                    or note.get("collects")
                ),
                comment_count=_intish(note.get("comment_count") or note.get("comments")),
                share_count=_intish(note.get("share_count") or note.get("shares")),
                description=str(
                    note.get("description") or note.get("desc") or note.get("text") or ""
                ),
                source_strategy=f"xhs-extension-{page_type}",
                content_id=note_id,
                content_url=best_url,
                source_platform="xiaohongshu",
                author_name=author,
                source_keyword_id=source_keyword_id,
                published_at=published.published_at,
                published_label=published.published_label,
            )
            writes.append(
                discovered_content_to_candidate_write(
                    item,
                    source_context=page_type,
                    raw_payload={
                        "note_id": note_id,
                        "url": best_url,
                        "page_type": page_type,
                        "title": title,
                        "author": author,
                        "cover_url": cover_url,
                        "admission_policy": "observed",
                    },
                )
            )
        if skipped_self > 0:
            logger.info(
                "xhs ingest filter: dropped %d self-authored note(s) (%s)",
                skipped_self,
                page_type,
            )
        if covers_saved > 0:
            logger.info(
                "xhs ingest: cached %d extension-harvested cover(s) (%s)",
                covers_saved,
                page_type,
            )
        if not writes:
            return 0
        try:
            return int(enqueue(writes, max_pending_per_source=_discovery_candidate_pending_cap()))
        except TypeError:
            return int(enqueue(writes))

    @app.post("/api/sources/xhs/observed-urls")
    async def ingest_xhs_observed_urls(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept xhs note URLs + optional metadata the extension collected.

        Body: ``{ "urls": [...], "notes": [{url, title, author, cover_url}], "page_type": "..." }``

        When ``notes`` is present, metadata is normalized into
        ``discovery_candidates``.  The shared discovery-candidate drain then
        evaluates and admits accepted notes through the same path as other
        platforms.
        """
        from fastapi import HTTPException

        urls_raw: list[str] = payload.get("urls", [])
        notes_raw: list[dict[str, Any]] = payload.get("notes", [])
        page_type: str = payload.get("page_type", "other")

        if not urls_raw and not notes_raw:
            raise HTTPException(status_code=422, detail="urls or notes must be non-empty")
        if len(urls_raw) > xhs_max_urls_per_batch:
            raise HTTPException(
                status_code=422,
                detail=f"Too many URLs (max {xhs_max_urls_per_batch})",
            )

        # v0.3.57+: passive collector (extension v0.3.10) piggybacks
        # self_info on every observed-urls request. Persist on first
        # arrival so subsequent requests without self_info still filter
        # via the loaded state.
        self_info_now = _extract_self_info_from_payload(payload)
        if self_info_now:
            _persist_xhs_self_info(self_info_now)
        self_info_for_filter = self_info_now or _load_xhs_self_info()

        # Filter to valid xhs note URLs. Search cards may now expose
        # ``/search_result/<id>`` in addition to ``/explore/<id>`` and the
        # legacy ``/discovery/item/<id>``; all three key on the same note id.
        valid_urls = [
            u
            for u in urls_raw
            if isinstance(u, str)
            and u.startswith(xhs_url_prefix)
            and ("/explore/" in u or "/discovery/item/" in u or "/search_result/" in u)
        ]

        # Store bare URLs for tracking
        if valid_urls:
            ctx.database.save_xhs_observed_urls(valid_urls, page_type)
            _backfill_xhs_tokens(ctx.database, valid_urls)

        # Store rich notes into the shared pending evaluation pool.
        enqueued = 0
        if notes_raw:
            enqueued = _cache_xhs_notes(
                ctx.database,
                notes_raw,
                page_type,
                self_info=self_info_for_filter or None,
            )
            if enqueued:
                _notify_discovery_candidates_enqueued("xiaohongshu")

        return {
            "ok": True,
            "accepted": len(valid_urls),
            "enqueued": enqueued,
        }

    @app.post("/api/sources/xhs/tokens", deprecated=True)
    async def ingest_xhs_tokens(payload: dict[str, Any]) -> dict[str, Any]:
        """Ingest ``(note_id, xsec_token)`` pairs harvested by the MAIN-
        world fetch sniffer inside ``dist/main/xhs-token-sniffer.js``.

        We rebuild the full tokenized URL from each pair and feed it
        through ``_backfill_xhs_tokens`` so previously-cached bare URLs
        (the typical search-page-sourced ones) get upgraded in place.
        Without this, clicking an xhs recommendation trips xhs's 300031
        access-denied gating because the stored URL lacks xsec_token.

        **Deprecated** in favour of
        ``POST /api/sources/xiaohongshu/credential`` with ``kind="token"``.
        Note what that ``kind`` is for: an ``xsec_token`` is a *content access*
        token for one note, not a login credential, and it says nothing about
        whether anyone is logged in (spec D5). Keeping it a distinct kind is
        what stops it from inheriting a login credential's promises.
        """
        raw = payload.get("pairs", [])
        if not isinstance(raw, list) or not raw:
            return {"ok": True, "upgraded": 0}
        result = await _write_source_credential(
            "xiaohongshu", kind="token", value=raw, source="extension"
        )
        return {"ok": True, "upgraded": result.upgraded}

    @app.post(
        "/api/sources/xhs/login-state",
        response_model=XhsLoginStateResponse,
        deprecated=True,
    )
    async def update_xhs_login_state(payload: XhsLoginStateIn) -> XhsLoginStateResponse:
        """Persist the extension-observed xhs login state without storing cookies.

        **Deprecated** in favour of
        ``POST /api/sources/xiaohongshu/credential`` with
        ``kind="login_state"``.
        """
        if not hasattr(ctx.database, "set_xhs_login_state"):
            raise HTTPException(status_code=503, detail="database not configured")
        result = await _write_source_credential(
            "xiaohongshu", kind="login_state", value=payload.logged_in, source="extension"
        )
        return XhsLoginStateResponse(
            ok=True,
            logged_in=payload.logged_in,
            updated_at=result.updated_at,
        )

    @app.post(
        "/api/sources/zhihu/login-state",
        response_model=ZhihuLoginStateResponse,
        deprecated=True,
    )
    async def update_zhihu_login_state(payload: ZhihuLoginStateIn) -> ZhihuLoginStateResponse:
        """Persist the extension-observed Zhihu login state without storing cookies.

        **Deprecated** in favour of ``POST /api/sources/zhihu/credential`` with
        ``kind="login_state"``.
        """
        if not hasattr(ctx.database, "set_zhihu_login_state"):
            raise HTTPException(status_code=503, detail="database not configured")
        result = await _write_source_credential(
            "zhihu", kind="login_state", value=payload.logged_in, source="extension"
        )
        return ZhihuLoginStateResponse(
            ok=True,
            logged_in=payload.logged_in,
            updated_at=result.updated_at,
        )

    @app.post(
        "/api/sources/linuxdo/login-state",
        response_model=LinuxdoLoginStateResponse,
        deprecated=True,
    )
    async def update_linuxdo_login_state(
        payload: LinuxdoLoginStateIn,
    ) -> LinuxdoLoginStateResponse:
        """Persist the extension-observed optional Linux.do session state."""
        if not hasattr(ctx.database, "set_linuxdo_login_state"):
            raise HTTPException(status_code=503, detail="database not configured")
        result = await _write_source_credential(
            "linuxdo", kind="login_state", value=payload.logged_in, source="extension"
        )
        return LinuxdoLoginStateResponse(
            ok=True,
            logged_in=payload.logged_in,
            updated_at=result.updated_at,
        )

    @app.post("/api/sources/v2ex/login-state", deprecated=True)
    async def update_v2ex_login_state(payload: dict[str, Any]) -> dict[str, Any]:
        """Persist only the V2EX browser login heartbeat.

        The extension may include a public username observed in the page nav;
        cookie values, headers and page HTML are deliberately not accepted.
        """
        logged_in = payload.get("logged_in")
        if not isinstance(logged_in, bool):
            raise HTTPException(status_code=422, detail="logged_in must be boolean")
        if not hasattr(ctx.database, "set_v2ex_login_state"):
            raise HTTPException(status_code=503, detail="database not configured")
        ctx.database.set_v2ex_login_state(logged_in)
        if not logged_in and hasattr(ctx.database, "clear_v2ex_browser_identity"):
            ctx.database.clear_v2ex_browser_identity()
        username = str(payload.get("username", "") or "").strip()
        if username and logged_in:
            from openbiliclaw.sources.v2ex_client import validate_v2ex_username

            try:
                username = validate_v2ex_username(username)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="invalid V2EX username") from exc
            if hasattr(ctx.database, "set_v2ex_browser_identity"):
                ctx.database.set_v2ex_browser_identity(username, evidence="observed")
        _, updated_at = ctx.database.get_v2ex_login_state()
        stored_username = ""
        if hasattr(ctx.database, "get_v2ex_browser_identity"):
            stored_username, _, _ = ctx.database.get_v2ex_browser_identity()
        return {
            "ok": True,
            "logged_in": logged_in,
            "username": stored_username,
            "updated_at": updated_at,
        }

    @app.post(
        "/api/sources/weibo/login-state",
        response_model=WeiboLoginStateResponse,
        deprecated=True,
    )
    async def update_weibo_login_state(payload: WeiboLoginStateIn) -> WeiboLoginStateResponse:
        """Persist only the extension's Weibo login boolean, never a Cookie."""
        if not hasattr(ctx.database, "set_weibo_login_state"):
            raise HTTPException(status_code=503, detail="database not configured")
        result = await _write_source_credential(
            "weibo", kind="login_state", value=payload.logged_in, source="extension"
        )
        return WeiboLoginStateResponse(
            ok=True,
            logged_in=payload.logged_in,
            updated_at=result.updated_at,
        )

    @app.post("/api/sources/v2ex/identity")
    async def ingest_v2ex_identity(payload: dict[str, Any]) -> dict[str, Any]:
        """Persist an observed username or an explicit user acceptance."""
        from openbiliclaw.sources.v2ex_client import validate_v2ex_username

        try:
            username = validate_v2ex_username(payload.get("username"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid V2EX username") from exc
        if not username:
            raise HTTPException(status_code=422, detail="invalid V2EX username")
        accepted = payload.get("accept") is True
        required_setter = "set_v2ex_accepted_identity" if accepted else "set_v2ex_browser_identity"
        if not hasattr(ctx.database, required_setter):
            raise HTTPException(status_code=503, detail="database not configured")
        if accepted:
            ctx.database.set_v2ex_accepted_identity(username)
            _, observed_at = ctx.database.get_v2ex_accepted_identity()
            evidence = "accepted"
        else:
            ctx.database.set_v2ex_browser_identity(username, evidence="observed")
            _, evidence, observed_at = ctx.database.get_v2ex_browser_identity()
        return {
            "ok": True,
            "username": username,
            "evidence": evidence,
            "observed_at": observed_at,
        }

    @app.get("/api/sources/v2ex/identity")
    def get_v2ex_identity() -> dict[str, Any]:
        """Return the local identity ladder and conflict gate without I/O."""
        from openbiliclaw.api.source_auth.probe_cache import LIVE_PROBES
        from openbiliclaw.config import load_config
        from openbiliclaw.sources.v2ex_identity import resolve_v2ex_identity_state

        result = resolve_v2ex_identity_state(
            cfg=load_config(),
            database=ctx.database,
            probes=LIVE_PROBES,
        ).as_dict()
        active_username = ""
        active_at = ""
        if hasattr(ctx.database, "get_v2ex_profile_identity"):
            active_username, active_at = ctx.database.get_v2ex_profile_identity()
        result["active_profile_identity"] = {
            "username": active_username,
            "activated_at": active_at,
        }
        result["identity_switch_required"] = bool(
            active_username
            and result.get("username")
            and active_username.casefold() != str(result["username"]).casefold()
        )
        return result

    # ── Bangumi extension identity channel ──────────────────────────
    # The content script on bgm.tv / bangumi.tv reads the page's public
    # ``CHOBITS_UID`` global (MAIN-world bridge) plus the nav's own
    # ``/user/<username>`` link and reports both. Only uid + username are
    # collected — both public, no cookies — so guided init can identify
    # the account with zero configuration when neither an access token
    # nor an explicit username was supplied.

    def _normalize_bangumi_identity(raw: Any) -> dict[str, str] | None:
        """Validate an extension-reported Bangumi identity payload.

        Returns ``{"uid": ..., "username": ...}`` when the uid is a positive
        integer; malformed usernames are dropped (treated as missing) rather
        than persisted, per the defensive-parse rule.
        """
        from openbiliclaw.sources.bangumi_client import validate_bangumi_username

        if not isinstance(raw, dict):
            return None
        try:
            uid = int(str(raw.get("uid", "") or "").strip())
        except (TypeError, ValueError):
            return None
        if uid <= 0:
            return None
        try:
            username = validate_bangumi_username(raw.get("username"))
        except ValueError:
            logger.warning("bangumi identity: dropping malformed username from extension report")
            username = ""
        return {"uid": str(uid), "username": username}

    def _persist_bangumi_identity(
        identity: dict[str, str], *, verified: bool
    ) -> dict[str, Any] | None:
        """Save the extension-reported identity into discovery runtime state.

        ``verified`` records whether bgm.tv positively confirmed the pair. It
        is persisted alongside uid/username so every consumer can tell a
        confirmed identity from a fail-open best-effort one instead of
        treating both as equally true (project rule 7), and it is sticky-true
        per identity so a later unreachable-bgm.tv report cannot erase a
        confirmation we already hold (see ``_sticky_record``).

        Returns the record as actually written, or ``None`` when it could not
        be persisted at all. Callers MUST NOT report a state this did not
        store — an unpersisted "verified" would be a claim the next read
        contradicts.
        """
        memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        if memory_manager is None:
            # No persistence backend: the report is dropped. Say so rather
            # than echoing a flag nothing will read back (rule 7).
            logger.warning(
                "bangumi identity: no memory manager; cannot persist uid=%s",
                identity.get("uid", ""),
            )
            return None

        def _sticky_record(previous: object) -> dict[str, Any]:
            """This round's record, upgraded to verified if ``previous`` proved it.

            Evidence of a successful cross-check belongs to a specific
            uid↔username pair and does not expire when bgm.tv is later
            unreachable — downgrading it would erase proof we already hold and
            make consumers call a genuinely checked identity "未经 bgm.tv 校验"
            (flipping the flag, and rewriting the file, on every hiccup). So
            the flag only ratchets up, and only for the SAME identity: a
            changed uid or username is a different claim the old evidence says
            nothing about, so it starts from this round's result.

            Inheritance also requires the previous record to be one the current
            rules could have produced, i.e. a non-empty username. Before the
            flag meant "confirmed", the 404 path wrote
            ``{"username": "", "verified": true}``; re-reporting that same 404
            user matched on uid and on username (both ``""``) and inherited the
            stale ``true``, pinning a record that contradicts the
            verified-implies-a-username invariant. An illegal record is not
            evidence, so it is not inherited.
            """
            sticky = bool(verified)
            previous_username = previous.get("username") if isinstance(previous, dict) else None
            if (
                not sticky
                and isinstance(previous, dict)
                and previous.get("verified") is True
                # A confirmed record always names someone; an empty username
                # with verified=true can only come from the superseded rules.
                and isinstance(previous_username, str)
                and previous_username
                and previous.get("uid") == identity["uid"]
                and previous_username == identity["username"]
            ):
                sticky = True
            return {**identity, "verified": sticky}

        record: dict[str, Any] = {}
        try:
            update_state = getattr(memory_manager, "update_discovery_runtime_state", None)
            if callable(update_state):

                def _mutate(runtime_state: dict[str, Any]) -> None:
                    # The merge runs ONLY here. update_json_state holds a
                    # process + file lock and re-reads from disk before calling
                    # us, so this sees the authoritative current value; a
                    # pre-read outside the lock could be stale by now and was
                    # how a concurrent confirmation used to get reported away.
                    record.update(_sticky_record(runtime_state.get("bangumi_self_info")))
                    runtime_state["bangumi_self_info"] = record

                # Deliberately no lock-free write-avoidance fast path.
                # update_json_state writes unconditionally, so re-reporting an
                # unchanged identity costs one idempotent rewrite — accepted,
                # because the only way to decide "unchanged" without the lock
                # is a snapshot that a concurrent writer can already have
                # invalidated, and answering from that snapshot contradicts
                # what the next read returns.
                update_state(_mutate)
            else:
                # Non-atomic fallback for runtimes without the atomic entry
                # point (stubs). Same merge, weaker concurrency guarantee.
                state = memory_manager.load_discovery_runtime_state()
                record.update(_sticky_record(state.get("bangumi_self_info")))
                state["bangumi_self_info"] = record
                memory_manager.save_discovery_runtime_state(state)
        except Exception:
            logger.exception(
                "Failed to persist bangumi identity (uid=%s); reporting it as not stored",
                identity.get("uid", ""),
            )
            return None
        logger.info(
            "bangumi identity persisted: uid=%s username=%r verified=%s",
            record.get("uid", ""),
            record.get("username", ""),
            record.get("verified"),
        )
        return record

    def _load_bangumi_identity() -> tuple[str, bool]:
        """Extension-reported Bangumi ``(username, verified)`` from runtime state.

        Returns ``("", False)`` on any miss. Records written before the
        ``verified`` flag existed have no way to prove they were cross-checked,
        so they read back as UNVERIFIED rather than silently claiming a check
        that may never have run — the next bgm.tv page view re-reports and
        upgrades the record in place.

        Records that cannot be legal under the current rules are normalised
        here too: a ``verified`` record with no username (what the superseded
        404 path used to write) reads back as unverified. Doing this on read as
        well as on write means an installation that never revisits bgm.tv
        still stops seeing the stale claim, instead of waiting for a report
        that may never come.
        """
        from openbiliclaw.sources.bangumi_client import validate_bangumi_username

        memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        if memory_manager is None:
            return "", False
        try:
            state = memory_manager.load_discovery_runtime_state()
            info = state.get("bangumi_self_info")
            if not isinstance(info, dict):
                return "", False
            username = validate_bangumi_username(info.get("username"))
            return username, bool(username) and info.get("verified") is True
        except Exception:
            logger.debug("bangumi identity: runtime state unavailable", exc_info=True)
            return "", False

    # Publish for start_guided_init's username fallback ladder — that route is
    # defined earlier in this factory, so it reads the hook off app.state at
    # request time (by then create_app has finished wiring everything).
    app.state.load_bangumi_identity = _load_bangumi_identity

    async def _verify_bangumi_identity(identity: dict[str, str]) -> tuple[dict[str, str], bool]:
        """Authoritatively cross-check an extension-reported identity.

        A real-page E2E (2026-07-18) showed a DOM-scraped username can be a
        timeline stranger's, so the DOM value is never trusted as-is. The
        anonymous public endpoint ``GET /v0/users/{username}`` returns the
        account's ``id``; users without a custom slug keep ``str(uid)`` as
        their username, so a uid-only report also resolves for them.

        Returns ``(identity, verified)``. ``verified`` is True only when bgm.tv
        actually answered — the caller persists that flag so a fail-open value
        is never mistaken for a checked one.

        ``verified`` is True **only when bgm.tv positively confirmed this
        uid↔username pair** — i.e. ``get_user`` returned and its ``id`` equals
        the reported uid, yielding a non-empty username. Everything else is
        False, including cases where bgm.tv answered clearly: a 404 *refutes*
        the username, it does not confirm who the uid belongs to, and a
        uid-only lookup that 404s never checked anything at all. Treating
        "bgm.tv replied" as "identity confirmed" would let sticky-true pin an
        account we never actually established as ``verified`` forever.

        Rules (never cache failures; ``trust_env=False`` via BangumiClient):
        - API ``id`` matches the reported uid → persist the API's username,
          VERIFIED.
        - Username resolves to a DIFFERENT id, or does not exist → keep the
          uid, DISCARD the username, log WARNING (plausible-but-wrong guard);
          NOT verified — a refutation is not a confirmation.
        - Network / upstream failure → accept the DOM username best-effort but
          mark it UNVERIFIED and log WARNING with the real cause. Staying
          fail-open keeps zero-config users working when bgm.tv is unreachable
          (bgm.tv sits behind overseas CF, and the default ``[network] mode =
          system`` only reaches it when the machine already has a working
          proxy), so the guard's honesty has to come from the flag + log rather
          than from rejecting the report.
        """
        from openbiliclaw.sources.bangumi_client import (
            BangumiAPIError,
            BangumiClient,
            validate_bangumi_username,
        )

        uid = identity["uid"]
        reported_username = identity["username"]
        lookup = reported_username or uid
        try:
            async with BangumiClient(request_interval_seconds=0) as bangumi_client:
                user = await bangumi_client.get_user(lookup)
        except BangumiAPIError as exc:
            if exc.code == "not_found":
                if reported_username:
                    logger.warning(
                        "bangumi identity: reported username %r does not exist; "
                        "keeping uid=%s only (NOT verified — a 404 refutes the "
                        "username, it does not confirm the uid's owner)",
                        reported_username,
                        uid,
                    )
                    return {"uid": uid, "username": ""}, False
                # uid-only report from a custom-slug user: bgm.tv answered, but
                # nothing about this uid's owner was established, so not verified.
                return identity, False
            logger.warning(
                "bangumi identity: could not verify uid=%s username=%r against bgm.tv "
                "(%s: %s); storing the extension report UNVERIFIED",
                uid,
                reported_username,
                exc.code,
                exc,
            )
            return identity, False
        except Exception as exc:
            logger.warning(
                "bangumi identity: could not verify uid=%s username=%r against bgm.tv "
                "(%s: %s); storing the extension report UNVERIFIED",
                uid,
                reported_username,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return identity, False
        api_id = user.get("id")
        try:
            api_uid = str(int(str(api_id).strip()))
        except (TypeError, ValueError):
            api_uid = ""
        if api_uid != uid:
            logger.warning(
                "bangumi identity: reported username %r belongs to uid %s, not %s; "
                "discarding username (NOT verified)",
                reported_username or lookup,
                api_uid or "?",
                uid,
            )
            return {"uid": uid, "username": ""}, False
        try:
            api_username = validate_bangumi_username(user.get("username"))
        except ValueError:
            api_username = ""
        confirmed_username = api_username or reported_username
        # Only a non-empty username makes this a confirmed *pair*. bgm.tv
        # returning an unusable username leaves nothing to have confirmed, so
        # the flag stays False rather than claiming a pair that does not exist.
        return {"uid": uid, "username": confirmed_username}, bool(confirmed_username)

    @app.post("/api/sources/bangumi/identity")
    async def ingest_bangumi_identity(payload: dict[str, Any]) -> dict[str, Any]:
        """Persist the extension-observed Bangumi account (uid + username).

        Both values are public page data; no cookies or tokens are accepted
        here. A payload without a positive uid is rejected so a logged-out
        page can never overwrite a previously known identity. The username is
        cross-checked against ``GET /v0/users/{username}`` before persisting —
        a mismatching or unknown username is dropped (uid kept) so a DOM
        drift can never store a stranger's identity. When bgm.tv is
        unreachable the report is still stored (zero-config users must keep
        working) but flagged ``verified: false``.

        The whole body is read back off the record that was actually written,
        so a 200 always describes what a subsequent read returns. If the
        identity could not be persisted the request fails with 500 instead of
        echoing a state nothing stored — the extension treats a failed report
        as best-effort and re-reports on the next bgm.tv page view.
        """
        identity = _normalize_bangumi_identity(payload)
        if identity is None:
            raise HTTPException(status_code=422, detail="uid must be a positive integer")
        identity, verified = await _verify_bangumi_identity(identity)
        stored = _persist_bangumi_identity(identity, verified=verified)
        if stored is None:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Bangumi identity could not be persisted; see server logs. "
                    "The next bgm.tv page view will re-report it."
                ),
            )
        return {
            "ok": True,
            "uid": stored["uid"],
            "username": stored["username"],
            "verified": stored["verified"],
        }

    # ── Bilibili extension search fallback endpoints ────────────────

    from openbiliclaw.sources.bili_tasks import (
        BiliTaskQueue,
        source_keyword_id_from_bili_task,
    )

    _bili_task_queue: BiliTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _bili_task_queue = BiliTaskQueue(ctx.database)

    @app.get("/api/sources/bili/next-task")
    def bili_next_task(response: Any = None) -> Any:
        """Claim and return the oldest runnable Bilibili extension task."""
        from starlette.responses import Response

        if _bili_task_queue is None:
            return Response(status_code=204)
        task = _bili_task_queue.next_pending()
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/bili/task-result")
    async def bili_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept Bilibili extension search results and enqueue candidates."""

        task_id = str(payload.get("task_id", "") or "").strip()
        status = str(payload.get("status", "") or "").strip()
        videos = [video for video in payload.get("videos", []) if isinstance(video, dict)]
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _bili_task_queue is None:
            return {"ok": True, "enqueued": 0}

        task = _bili_task_queue.get(task_id)
        task_payload_json = str(task.get("payload_json") or "") if task else ""

        if status in {"partial", "ok", "empty"}:
            is_final = status in {"ok", "empty"}
            added_videos = _bili_task_queue.merge_result(
                task_id,
                videos=videos if videos else None,
                debug=debug,
                complete=is_final,
            )
            if is_final:
                _mark_bili_task_keyword_terminal(task_payload_json, success=status == "ok")
            task_payload: dict[str, Any] = {}
            if task_payload_json:
                with suppress(Exception):
                    parsed = json.loads(task_payload_json)
                    if isinstance(parsed, dict):
                        task_payload = parsed
            query = str(task_payload.get("query") or task_payload.get("keyword") or "").strip()
            source_keyword_id = source_keyword_id_from_bili_task(task_payload_json)
            discovery_lane = (
                "recent"
                if str(task_payload.get("discovery_lane") or "").strip().lower() == "recent"
                else ""
            )
            enqueued = 0
            if added_videos:
                enqueued = _cache_bili_search_videos(
                    ctx.database,
                    added_videos,
                    query=query,
                    source_keyword_id=source_keyword_id,
                    discovery_lane=discovery_lane,
                )
                if enqueued:
                    _notify_discovery_candidates_enqueued("bilibili")
            return {"ok": True, "enqueued": enqueued}

        _bili_task_queue.fail(task_id, error=str(payload.get("error", "") or ""), debug=debug)
        _mark_bili_task_keyword_terminal(task_payload_json, success=False)
        return {"ok": True, "enqueued": 0}

    @app.post("/api/sources/bili/kick")
    async def bili_task_kick() -> dict[str, Any]:
        """Broadcast `bili_task_available` over runtime-stream."""
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "bili_task_available", "source": "task_kick"})
        return {"ok": True}

    def _claim_extension_native_task(slug: str) -> dict[str, Any] | None:
        broker = getattr(ctx, "extension_native_save_broker", None)
        if broker is None:
            return None
        job = broker.claim_next(slug)
        if job is None:
            return None
        return {
            "id": job.job_id,
            "type": "native_save",
            "item_key": job.item_key,
            "platform": job.platform,
            "platform_slug": job.platform_slug,
            "content_id": job.content_id,
            "content_url": job.content_url,
            "content_type": job.content_type,
            "requested_action": job.requested_action,
            "resolved_action": job.resolved_action,
            "target_label": job.target_label,
        }

    def _is_extension_native_job(task_id: str, slug: str | None = None) -> bool:
        broker = getattr(ctx, "extension_native_save_broker", None)
        return bool(broker is not None and broker.owns(task_id, slug))

    def _submit_extension_native_result(slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        from pydantic import ValidationError

        try:
            result = ExtensionNativeSaveResultIn.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid native-save result") from exc
        broker = getattr(ctx, "extension_native_save_broker", None)
        accepted = bool(
            broker is not None
            and broker.submit_result(
                slug,
                BrokerExtensionNativeSaveResultIn(
                    task_id=result.task_id,
                    item_key=result.item_key,
                    status=result.status,
                    error_code=result.error_code,
                    error_message=result.error_message,
                ),
            )
        )
        if not accepted:
            raise HTTPException(status_code=409, detail="native_save_result_conflict")
        return {"ok": True}

    def _require_legacy_task(queue: Any, task_id: str) -> dict[str, Any]:
        task = queue.get(task_id) if queue is not None else None
        if task is None:
            raise HTTPException(status_code=409, detail="task_result_conflict")
        return cast("dict[str, Any]", task)

    async def _kick_source_task(slug: str) -> dict[str, Any]:
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": f"{slug}_task_available", "source": "task_kick"})
        return {"ok": True}

    # ── XHS task queue endpoints (extension dispatcher) ──────────────

    from openbiliclaw.sources.xhs_tasks import (
        XhsCreatorStore,
        XhsTaskQueue,
        xhs_bootstrap_note_key,
        xhs_bootstrap_notes_to_events,
    )

    # Guard: only initialise when ctx.database is a real Database (has .conn).
    # Tests that pass database=object() as a stub won't trigger table creation.
    _xhs_task_queue: XhsTaskQueue | None = None
    _xhs_creator_store: XhsCreatorStore | None = None
    if hasattr(ctx.database, "conn"):
        _xhs_task_queue = XhsTaskQueue(ctx.database)
        _xhs_creator_store = XhsCreatorStore(ctx.database)

    @app.get("/api/sources/xhs/next-task")
    def xhs_next_task(response: Any = None) -> Any:
        """Claim and return the oldest runnable xhs task, or 204 if none."""
        from starlette.responses import Response

        xhs_cfg = getattr(
            getattr(getattr(ctx, "config", None), "sources", None),
            "xiaohongshu",
            None,
        )
        scheduler_cfg = getattr(getattr(ctx, "config", None), "scheduler", None)
        background_tasks_enabled = bool(getattr(xhs_cfg, "enabled", False)) and bool(
            getattr(scheduler_cfg, "enabled", True)
        )
        _cancel_disabled_source_incremental_tasks("xhs")

        if _xhs_task_queue is not None:
            cooldown = _xhs_task_queue.cooldown_remaining_seconds()
            if cooldown > 0:
                return Response(
                    status_code=204,
                    headers={"Retry-After": str(cooldown)},
                )

        native_task = _claim_extension_native_task("xhs")
        if native_task is not None:
            return native_task

        # Disabling a source stops automatic discovery immediately, including
        # tasks that were already pending before the config hot-reload. Keep
        # them pending so re-enabling can resume rather than silently discard
        # user-planned work. Explicit native-save jobs above remain available.
        if not background_tasks_enabled:
            return Response(status_code=204)

        # 204 No Content responses MUST NOT carry a body (RFC 7230).
        # JSONResponse(204, None) serialises None to "null" (4 bytes),
        # then GZipMiddleware (minimum_size=0) wraps it into ~20 bytes
        # of gzip stream while Content-Length stays at 4, which trips
        # h11's strict "Too much data for declared Content-Length"
        # check on every poll. Use a body-less Response instead.
        if _xhs_task_queue is None:
            return Response(status_code=204)
        try:
            task_interval_seconds = max(
                0,
                int(getattr(xhs_cfg, "task_interval_seconds", 1200)),
            )
        except (TypeError, ValueError):
            task_interval_seconds = 1200
        task = _xhs_task_queue.next_pending(
            only_ids=_init_owned_ids_filter(),
            min_interval_seconds=task_interval_seconds,
        )
        if task is None:
            delay = int(
                _xhs_task_queue.runtime_state().get(
                    "next_claim_delay_seconds",
                    0,
                )
                or 0
            )
            return Response(
                status_code=204,
                headers={"Retry-After": str(delay)} if delay > 0 else None,
            )

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/xhs/task-result")
    async def xhs_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a task result from the extension dispatcher."""
        task_id = str(payload.get("task_id", "") or "").strip()
        if not task_id:
            raise HTTPException(status_code=422, detail="task_id is required")
        status = str(payload.get("status", "") or "").strip()
        if _is_extension_native_job(task_id):
            if not _is_extension_native_job(task_id, "xhs"):
                raise HTTPException(status_code=409, detail="task_result_conflict")
            result = _submit_extension_native_result("xhs", payload)
            if status == "rate_limited" and _xhs_task_queue is not None:
                _xhs_task_queue.record_rate_limit(
                    error=str(payload.get("error_code", "") or "xhs_rate_limited"),
                )
            return result

        urls = payload.get("urls", [])
        if not isinstance(urls, list):
            urls = []
        notes = [note for note in payload.get("notes", []) if isinstance(note, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        legacy_queue = _xhs_task_queue
        if legacy_queue is None:
            raise HTTPException(status_code=409, detail="task_result_conflict")
        task = _require_legacy_task(legacy_queue, task_id)
        task_type = str(task.get("type", "")).strip() if task else ""
        task_status = str(task.get("status", "")).strip()
        if task_status in {"completed", "failed"}:
            # Terminal rows are immutable. In particular, never ingest fields
            # from a changed retry payload after the canonical result closed.
            return {"ok": True, "ignored": True}
        from openbiliclaw.sources.task_result_protocol import (
            parse_task_result,
            staged_terminal_status,
        )

        canonical_result = parse_task_result(task.get("result_json"))
        staged_status = staged_terminal_status(canonical_result)

        if status == "rate_limited" and not staged_status:
            legacy_queue.record_rate_limit(
                task_id,
                error=str(payload.get("error", "") or "xhs_rate_limited"),
                urls=urls,
                notes=notes if notes else None,
                scope_counts=scope_counts,
                debug=debug,
            )
            rate_limited_task = legacy_queue.get(task_id) or {}
            if str(rate_limited_task.get("status") or "").strip() == "failed":
                requeue_keyword_from_xhs_rate_limit(
                    ctx.database,
                    task.get("payload_json") if task is not None else None,
                )
        elif (
            staged_status
            or status in {"partial", "ok"}
            or (status == "empty" and task_type == "bootstrap_profile")
        ):
            is_final = (
                bool(staged_status)
                or status == "ok"
                or (status == "empty" and task_type == "bootstrap_profile")
            )
            incoming_self_info = _extract_self_info_from_payload(payload)
            merge_debug = dict(debug or {})
            if incoming_self_info is not None:
                merge_debug["_source_self_info"] = incoming_self_info
            if staged_status:
                # A previous attempt durably staged the first final payload.
                # Ignore every field on this retry and repair from that row.
                canonical_result = parse_task_result(task.get("result_json"))
            elif is_final:
                canonical_result = legacy_queue.stage_final_result(
                    task_id,
                    terminal_status=str(status),
                    urls=urls,
                    notes=notes if notes else None,
                    scope_counts=scope_counts,
                    debug=merge_debug or None,
                )
            else:
                legacy_queue.merge_result_with_enrichment(
                    task_id,
                    urls=urls,
                    notes=notes if notes else None,
                    scope_counts=scope_counts,
                    debug=merge_debug or None,
                    complete=False,
                )
                canonical_task = legacy_queue.get(task_id) or {}
                canonical_result = parse_task_result(canonical_task.get("result_json"))

            canonical_urls = [
                value for value in canonical_result.get("urls", []) if isinstance(value, str)
            ]
            canonical_notes = [
                value for value in canonical_result.get("notes", []) if isinstance(value, dict)
            ]
            canonical_debug = canonical_result.get("debug")
            canonical_debug = canonical_debug if isinstance(canonical_debug, dict) else {}
            # v0.3.48+: piggyback self_info from bootstrap debug payload.
            # v0.3.57+: also accept self_info at the payload top level for
            # search / creator / passive paths via extension v0.3.10.
            # Persist immediately so future requests can also consult it,
            # AND use the just-extracted value in this request's
            # downstream filters (skip a state round-trip that some
            # in-process test stubs don't implement).
            canonical_self_info = _normalize_self_info(
                canonical_debug.get("_source_self_info")
            ) or _extract_self_info_from_payload(canonical_result)
            if canonical_self_info:
                _persist_xhs_self_info(canonical_self_info)
            self_info_now = canonical_self_info or _load_xhs_self_info()
            # gui-init D1: the result is always persisted above (merge_result)
            # so init's own collector can read it. During an active init:
            #  - skip ALL live discovery-pool writes (stage 4 owns the pool);
            #  - skip profile propagation for tasks NOT owned by this run (stale
            #    / steady-state completions), but DO propagate init-OWNED
            #    bootstrap results through the normal deduped path so the source
            #    signals land in memory exactly once (handles force re-init).
            # Computed BEFORE the URL/token backfill (_backfill_xhs_tokens writes
            # content_cache + discovery_candidates).
            _init_busy = _init_active_now()
            _skip_profile = _init_busy and not _init_owns_task(task_id)
            # Store discovered URLs + metadata
            valid_urls = [u for u in canonical_urls if u.startswith(xhs_url_prefix)]
            if valid_urls and not _init_busy:
                ctx.database.save_xhs_observed_urls(valid_urls, "task")
                _backfill_xhs_tokens(ctx.database, valid_urls)
            candidate_notes = canonical_notes
            if candidate_notes and not _init_busy:
                # P1.8: a planner-driven xhs *search* task carries its
                # ``source_keyword_id`` on the payload → thread it onto the
                # ingested candidates so admission backfills the keyword's yield.
                # Passive / non-search tasks have no id → plain None.
                task_source_keyword_id = (
                    source_keyword_id_from_xhs_task(task.get("payload_json"))
                    if task is not None
                    else None
                )
                enqueued = _cache_xhs_notes(
                    ctx.database,
                    candidate_notes,
                    "task",
                    self_info_now,
                    source_keyword_id=task_source_keyword_id,
                )
                if enqueued:
                    _notify_discovery_candidates_enqueued("xiaohongshu")
            if task_type == "bootstrap_profile" and canonical_notes and not _skip_profile:
                fresh_notes, note_keys_by_index = _filter_new_source_bootstrap_items(
                    "xhs",
                    canonical_notes,
                    xhs_bootstrap_note_key,
                )
                # Filter self-authored notes from event propagation —
                # otherwise the user's own posts get treated as their
                # own "favorite/like" signals and warp the soul profile.
                skipped_self = 0
                events_with_keys: list[tuple[dict[str, Any], str]] = []
                for index, note in enumerate(fresh_notes):
                    if _is_self_authored_note(note, self_info_now):
                        skipped_self += 1
                        continue
                    for event in xhs_bootstrap_notes_to_events([note]):
                        key = note_keys_by_index.get(index, "")
                        events_with_keys.append((event, key))
                accepted_keys = await _accept_source_profile_events(
                    source="xhs",
                    task_id=task_id,
                    events_with_keys=events_with_keys,
                    generic_owner=not _init_busy,
                )
                _mark_source_bootstrap_keys("xhs", accepted_keys)
                if skipped_self > 0:
                    logger.info(
                        "xhs bootstrap propagate: dropped %d self-authored note(s) (%d propagated)",
                        skipped_self,
                        len(accepted_keys),
                    )
            if is_final:
                # Keyword lifecycle is also downstream of the canonical row;
                # perform it before the one-way terminal flip so retry can
                # repair every intermediate failure.
                if task is not None:
                    mark_keyword_terminal_from_xhs_task(
                        ctx.database,
                        task.get("payload_json"),
                        success=True,
                    )
                legacy_queue.complete_staged_result(task_id)
        else:
            # Search/creator ``empty`` is retryable and remains a failed task,
            # but never persist a blank error: it made real selector drift look
            # indistinguishable from an unexplained transport failure.
            failure_error = str(payload.get("error", "") or "").strip()
            if not failure_error and status == "empty":
                failure_error = "xhs_empty_result"
            failed = legacy_queue.fail(task_id, error=failure_error, debug=debug)
            if failure_error == "xhs_login_required" and hasattr(
                ctx.database, "set_xhs_login_state"
            ):
                # Cookie presence is only a hint: XHS can retain web_session
                # while the rendered page already presents its login gate.
                # The task page is fresher, direct browser evidence.
                ctx.database.set_xhs_login_state(False)
            # Unified keyword planner lifecycle (P1.7): the async search failed →
            # mark its ``source_keyword_id`` word ``failed`` (retry via attempts).
            if failed and task is not None:
                mark_keyword_terminal_from_xhs_task(
                    ctx.database, task.get("payload_json"), success=False
                )

        return {"ok": True}

    @app.get("/api/sources/xhs/creators")
    def xhs_list_creators() -> dict[str, Any]:
        """List all xhs creator subscriptions."""
        if _xhs_creator_store is None:
            return {"items": []}
        return {"items": _xhs_creator_store.list_all()}

    @app.post("/api/sources/xhs/creators", status_code=201)
    def xhs_add_creator(payload: dict[str, Any]) -> dict[str, Any]:
        """Add an xhs creator subscription."""
        from fastapi import HTTPException

        creator_id = payload.get("creator_id", "")
        creator_url = payload.get("creator_url", "")
        display_name = payload.get("display_name", "")

        if not creator_id or not creator_url:
            raise HTTPException(
                status_code=422,
                detail="creator_id and creator_url are required",
            )

        if _xhs_creator_store is None:
            raise HTTPException(status_code=503, detail="xhs not configured")
        _xhs_creator_store.add(creator_id, creator_url, display_name)
        return {"ok": True}

    @app.delete("/api/sources/xhs/creators/{sub_id}")
    def xhs_delete_creator(sub_id: int) -> dict[str, Any]:
        """Delete an xhs creator subscription."""
        from fastapi import HTTPException

        if _xhs_creator_store is None:
            raise HTTPException(status_code=503, detail="xhs not configured")
        deleted = _xhs_creator_store.delete(sub_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return {"ok": True}

    # ── X (Twitter) account subscriptions ──────────────────────────
    # No extension round-trip: the X producer fetches each subscription
    # server-side via XCreatorStrategy. This block only owns the
    # x_creator_subscriptions table + CRUD (mirrors the XHS creators above).

    from openbiliclaw.sources.x_tasks import XCreatorStore, normalize_handle

    _x_creator_store: XCreatorStore | None = None
    if hasattr(ctx.database, "conn"):
        _x_creator_store = XCreatorStore(ctx.database)

    @app.get("/api/sources/x/creators")
    def x_list_creators() -> dict[str, Any]:
        """List all X account subscriptions."""
        if _x_creator_store is None:
            return {"items": []}
        return {"items": _x_creator_store.list_all()}

    @app.post("/api/sources/x/creators", status_code=201)
    def x_add_creator(payload: dict[str, Any]) -> dict[str, Any]:
        """Add an X account subscription (idempotent; leading @ stripped)."""
        from fastapi import HTTPException

        handle = normalize_handle(str(payload.get("handle", "")))
        if not handle:
            raise HTTPException(status_code=422, detail="handle is required")

        if _x_creator_store is None:
            raise HTTPException(status_code=503, detail="x not configured")
        _x_creator_store.add(handle)
        return {"ok": True}

    @app.delete("/api/sources/x/creators/{sub_id}")
    def x_delete_creator(sub_id: int) -> dict[str, Any]:
        """Delete an X account subscription."""
        from fastapi import HTTPException

        if _x_creator_store is None:
            raise HTTPException(status_code=503, detail="x not configured")
        deleted = _x_creator_store.delete(sub_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return {"ok": True}

    # ── X (Twitter) source health (spec §7) ────────────────────────
    # Surfaces the persisted health state machine so the settings UI can
    # show login / rate-limit / block status (rendered in Task 12).

    @app.get("/api/sources/x/status", response_model=XStatusResponse)
    def x_source_status() -> XStatusResponse:
        """Return the current X source health (ok / cookie / rate-limit / block)."""
        if not hasattr(ctx.database, "conn"):
            return XStatusResponse()
        from openbiliclaw.storage.x_health import XSourceHealthStore

        health = XSourceHealthStore(ctx.database).get()
        return XStatusResponse(
            state=str(health.get("state", "ok")),
            consecutive_failures=int(health.get("consecutive_failures", 0)),
            feed_paused=bool(health.get("feed_paused", False)),
            cooldown_until=str(health.get("cooldown_until", "")),
            detail=str(health.get("detail", "")),
            updated_at=str(health.get("updated_at", "")),
        )

    def _bangumi_status_item(cfg: Any, auth_ctx: Any) -> SourceStatusItem:
        """Bangumi's status item: the auth contract plus its two extra axes.

        Bangumi's *auth* verdict now comes from ``auth_bangumi`` like every other
        source (``auth_required=False`` — anonymous-public — with an optional,
        live-verifiable personal token). But it carries two dimensions the
        uniform ``SourceStatusItem`` the loop builds cannot express, so it is
        assembled here rather than inline in ``sources_status()``:

        * a **discovery-health** ``detail`` (``尚未运行`` / 退避冷却 / run outcomes)
          — the chip the settings page renders, distinct from the contract's own
          auth-focused ``detail``. Keeping it means moving to the contract does
          not silently drop the discovery status users already saw.
        * the **``token_state``** axis (``ok`` / ``rejected`` / ``""``), which the
          frontend overlays as 「令牌已失效」 when discovery rejected the token.

        ``state`` / ``logged_in`` follow the contract (``no_auth`` / ``True``): the
        discovery-health string lives in ``detail`` now, not in ``state``, so the
        legacy field stops conflating scheduling and discovery with auth
        readiness — the D1 conflation the contract exists to remove. The
        ``enabled`` switch stays its own field, never folded back into ``state``.
        """
        from openbiliclaw.api.source_auth.providers import auth_bangumi
        from openbiliclaw.runtime.bangumi_producer import (
            bangumi_disabled_detail,
            bangumi_source_status,
        )

        srcs = cfg.sources
        bgm_enabled = bool(getattr(getattr(srcs, "bangumi", None), "enabled", False))
        bgm_token_configured = bool(
            str(getattr(getattr(srcs, "bangumi", None), "access_token", "") or "").strip()
        )
        bgm_username_configured = bool(
            str(getattr(getattr(srcs, "bangumi", None), "username", "") or "").strip()
        )
        bgm_status = (
            bangumi_source_status(
                ctx.database,
                enabled=bgm_enabled,
                token_configured=bgm_token_configured,
                username_configured=bgm_username_configured,
            )
            if hasattr(ctx.database, "conn")
            else {
                "state": "unverified" if bgm_enabled else "disabled",
                # No ledger to read here, so a saved token can only be reported
                # as "ok" — but the disabled wording still has to name it.
                "detail": (
                    "Bangumi 使用官方公开 API，尚未运行内容发现。"
                    if bgm_enabled
                    else bangumi_disabled_detail(
                        token_state="ok" if bgm_token_configured else "",
                        username_configured=bgm_username_configured,
                    )
                ),
                **({"token_state": "ok"} if bgm_token_configured else {}),
            }
        )
        # ``state`` / ``logged_in`` now come from the auth contract (always
        # ``no_auth`` / ``True`` for an anonymous-public source); the
        # discovery-health string moves to ``detail``, and ``token_state`` stays
        # its own axis. The contract itself carries the optional-token verdict.
        return SourceStatusItem(
            enabled=bgm_enabled,
            state="no_auth",
            detail=str(bgm_status.get("detail") or "Bangumi 使用官方公开 API。"),
            logged_in=True,
            token_state=str(bgm_status.get("token_state") or ""),
            auth=auth_bangumi(auth_ctx),
        )

    def _attach_network_hints(statuses: SourcesStatusResponse, cfg: Any) -> None:
        """Attach the overseas-egress advisory to every source in *statuses*.

        A separate pass from assembling the auth status, and separate on
        purpose: this one is uniform over all families and reads a table in
        ``sources.platforms``, so it never grows a per-platform branch the way
        the aggregator it sits next to once did. The platform list AND both
        wordings live in that table; the settings surfaces render
        ``network_hint`` verbatim and never learn a platform name
        (``tests/test_source_network_hints.py`` pins that).
        """
        network_mode = str(getattr(getattr(cfg, "network", None), "mode", "") or "")
        for family in CANONICAL_SOURCE_FAMILIES:
            item = getattr(statuses, family, None)
            if not isinstance(item, SourceStatusItem):
                continue
            item.requires_overseas_network = requires_overseas_network(family)
            item.network_hint = overseas_network_hint(family, network_mode=network_mode)

    def _weibo_status_item(cfg: Any, auth_ctx: Any) -> SourceStatusItem:
        """Combine anonymous auth readiness with the latest local discovery run."""

        from openbiliclaw.api.source_auth.providers import auth_weibo
        from openbiliclaw.runtime.weibo_producer import weibo_source_status

        enabled = bool(getattr(getattr(cfg.sources, "weibo", None), "enabled", False))
        status = (
            weibo_source_status(
                ctx.database,
                enabled=enabled,
                source_modes=getattr(
                    getattr(cfg.sources, "weibo", None),
                    "source_modes",
                    ("search", "hot", "creator"),
                ),
            )
            if hasattr(ctx.database, "conn")
            else {
                "state": "unverified" if enabled else "disabled",
                "detail": "尚未运行微博内容发现。" if enabled else "微博来源未启用。",
            }
        )
        raw_discovery_state = str(status.get("state") or "unverified")
        if raw_discovery_state not in {
            "disabled",
            "unverified",
            "ready",
            "partial",
            "error",
            "rate_limited",
        }:
            raw_discovery_state = "unverified"
        discovery_state = cast(
            "Literal['disabled', 'unverified', 'ready', 'partial', 'error', 'rate_limited']",
            raw_discovery_state,
        )
        status_detail = str(status.get("detail") or "微博公开发现可匿名。")
        if enabled:
            status_detail += " 初始化本人事件需要浏览器登录态。"
        return SourceStatusItem(
            enabled=enabled,
            state="no_auth",
            detail=status_detail,
            logged_in=True,
            feed_paused=discovery_state == "rate_limited",
            discovery_state=discovery_state,
            auth=auth_weibo(auth_ctx),
        )

    def _github_status_item(cfg: Any, auth_ctx: Any) -> SourceStatusItem:
        """Combine GitHub's anonymous auth contract with local discovery health."""

        from openbiliclaw.api.source_auth.providers import auth_github
        from openbiliclaw.runtime.github_producer import github_source_status
        from openbiliclaw.sources.github_client import resolve_github_access_token

        github_cfg = getattr(cfg.sources, "github", None)
        enabled = bool(getattr(github_cfg, "enabled", False))
        access_token, _origin = resolve_github_access_token(
            config_token=str(getattr(github_cfg, "access_token", "") or ""),
        )
        status = (
            github_source_status(
                ctx.database,
                enabled=enabled,
                access_token=access_token,
                source_modes=getattr(
                    github_cfg,
                    "source_modes",
                    ("search", "ranked", "latest"),
                ),
            )
            if hasattr(ctx.database, "conn")
            else {
                "state": "unverified" if enabled else "disabled",
                "detail": (
                    "尚未运行 GitHub 仓库发现。"
                    if enabled
                    else "已保存 GitHub PAT，但来源未启用；启用并保存后才会使用。"
                    if access_token
                    else "GitHub 来源未启用。"
                ),
                **({"token_state": "ok"} if access_token else {}),
            }
        )
        raw_discovery_state = str(status.get("state") or "unverified")
        if raw_discovery_state not in {
            "disabled",
            "unverified",
            "ready",
            "partial",
            "error",
            "rate_limited",
        }:
            raw_discovery_state = "unverified"
        discovery_state = cast(
            "Literal['disabled', 'unverified', 'ready', 'partial', 'error', 'rate_limited']",
            raw_discovery_state,
        )
        auth_contract = auth_github(auth_ctx)
        return SourceStatusItem(
            enabled=enabled,
            state="no_auth",
            # Legacy fields stay byte-for-byte provider-owned; discovery
            # health is carried independently by discovery_state/feed_paused.
            detail=auth_contract.detail,
            logged_in=True,
            feed_paused=discovery_state == "rate_limited",
            discovery_state=discovery_state,
            token_state=str(status.get("token_state") or "") if enabled else "",
            auth=auth_contract,
        )

    @app.get("/api/sources/status", response_model=SourcesStatusResponse)
    def sources_status() -> SourcesStatusResponse:
        """Unified per-source login / cookie readiness for the settings pages.

        Local-only: every verdict comes from config, local credential files,
        the extension's login heartbeats, the X health store and cached probe
        results — this handler makes **no outbound platform request**, which
        matters because open settings pages poll it every ~30s.

        Each platform's logic lives in one provider in
        ``api/source_auth/providers.py``; this endpoint only walks the registry
        and assembles the response. The legacy ``state`` / ``logged_in`` /
        ``detail`` fields are carried verbatim from each provider (Wave A
        promises byte-identical output), while the new ``auth`` sub-object
        exposes the same knowledge as independent dimensions. See
        :class:`SourceAuthContract` and ``source_auth/legacy.py``.

        Bangumi is answered by ``auth_bangumi`` like the rest, but its item is
        re-assembled by ``_bangumi_status_item`` to carry two axes the uniform
        item cannot: a discovery-health ``detail`` and the ``token_state`` chip.
        """
        from openbiliclaw.api.source_auth.providers import (
            SOURCE_AUTH_PROVIDERS,
            SourceAuthContext,
            source_enabled,
            source_feed_paused,
        )
        from openbiliclaw.config import load_config

        cfg = _pin_active_runtime_config(load_config())
        auth_ctx = SourceAuthContext(cfg=cfg, database=ctx.database)
        items: dict[str, SourceStatusItem] = {}
        for slug, provider in SOURCE_AUTH_PROVIDERS.items():
            contract = provider(auth_ctx)
            items[slug] = SourceStatusItem(
                enabled=source_enabled(auth_ctx, slug),
                state=contract.legacy_state,
                detail=contract.detail,
                logged_in=contract.legacy_logged_in,
                feed_paused=source_feed_paused(auth_ctx, slug),
                auth=contract,
            )
        xhs_runtime_state = (
            _xhs_task_queue.runtime_state()
            if _xhs_task_queue is not None
            else {"rate_limited": False}
        )
        xhs_item = items["xiaohongshu"]
        if xhs_item.enabled and bool(xhs_runtime_state.get("rate_limited")):
            remaining_seconds = int(xhs_runtime_state.get("cooldown_remaining_seconds", 0) or 0)
            remaining_minutes = max(1, (remaining_seconds + 59) // 60)
            rate_limit_strikes = max(
                1,
                int(xhs_runtime_state.get("rate_limit_strikes", 1) or 1),
            )
            xhs_item.state = "rate_limited"
            xhs_item.detail = (
                f"小红书连续第 {rate_limit_strikes} 次触发平台风控，后台任务已自动暂停，"
                f"约 {remaining_minutes} 分钟后恢复领取。"
            )
            xhs_item.feed_paused = True
        # Bangumi's uniform item (built above) is replaced: it carries a
        # discovery-health detail and the token_state axis the loop cannot model.
        # Do not delete this line thinking the loop covers it — it does not.
        items["bangumi"] = _bangumi_status_item(cfg, auth_ctx)
        items["github"] = _github_status_item(cfg, auth_ctx)
        items["weibo"] = _weibo_status_item(cfg, auth_ctx)
        statuses = SourcesStatusResponse(**items)
        _attach_network_hints(statuses, cfg)
        return statuses

    @app.post("/api/sources/{slug}/verify", response_model=SourceVerifyResponse)
    async def verify_source_credential(slug: str) -> SourceVerifyResponse:
        """Verify one source's credential on demand and return its fresh contract.

        The counterpart to ``GET /api/sources/status``: that route is polled and
        therefore never goes out, while this one is an explicit user action and
        is the only place a platform is probed. Which of the five verification
        actions runs is a fixed per-platform property — see
        ``source_auth/verify.py`` for why it is not keyed off the contract's
        ``verify_method``.

        Always 200 for a known slug, including when the answer is "could not
        tell": an unreachable proxy or a disconnected extension is not a client
        error, and turning it into one would strip the frontends of the message
        explaining what to do about it.
        """
        from openbiliclaw.api.source_auth.verify import verify_source
        from openbiliclaw.config import load_config

        try:
            result = await verify_source(
                slug,
                cfg=_pin_active_runtime_config(load_config()),
                database=ctx.database,
                event_hub=getattr(ctx, "event_hub", None),
            )
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown source: {slug}") from None

        return SourceVerifyResponse(
            slug=result.slug,
            outcome=result.outcome,
            changed=result.changed,
            message=result.message,
            replayed=result.replayed,
            retry_after_seconds=result.retry_after_seconds,
            auth=result.contract,
        )

    def _xhs_token_urls(pairs: Any) -> list[str]:
        """Rebuild tokenised note URLs from ``(note_id, xsec_token)`` pairs."""
        urls: list[str] = []
        for pair in pairs if isinstance(pairs, list) else []:
            if not isinstance(pair, dict):
                continue
            # Guard against the noise the sniffer's deep-walk can surface — e.g.
            # 24-hex ids that aren't notes. The backfill UPDATE is narrow (bvid
            # match), so a false id is a no-op, but the token must be non-empty.
            note_id = str(pair.get("note_id", "") or "").strip()
            token = str(pair.get("xsec_token", "") or "").strip()
            if not note_id or not token:
                continue
            urls.append(f"{xhs_url_prefix}explore/{note_id}?xsec_token={token}")
        return urls

    def _source_auth_contract(slug: str) -> SourceAuthContract:
        """Freshly recomputed contract for *slug*, or an empty one if unknown."""
        from openbiliclaw.api.source_auth.contract import SourceAuthContract
        from openbiliclaw.api.source_auth.providers import (
            SOURCE_AUTH_PROVIDERS,
            SourceAuthContext,
        )
        from openbiliclaw.config import load_config

        provider = SOURCE_AUTH_PROVIDERS.get(slug)
        if provider is None:
            return SourceAuthContract()
        return provider(
            SourceAuthContext(
                cfg=_pin_active_runtime_config(load_config()),
                database=ctx.database,
            )
        )

    def _credential_landed(slug: str, *, verdict: Any, value: str, changed: bool) -> None:
        """Bookkeeping every credential write owes once the value is really stored.

        Both write surfaces call this, and neither may skip it — the two steps
        below used to be done by ``POST /api/sources/{slug}/credential`` alone,
        so one cookie ended up in two different states depending on which page
        the user had open (invariant I5, the outcome half rather than the
        validation half):

        1. **Record the verdict the gate already paid for.** ``PUT /api/config``
           genuinely probed the platform and genuinely refused what failed, then
           dropped the answer — leaving the status chip on ``unverified`` for a
           credential the backend had just watched log in.
        2. **Drop the platform's debounced verify result.** It describes the
           credential that was there a moment ago; replaying it would tell a
           user their fix had not taken.

        A verdict served *from the cache* is not re-recorded: it would extend
        its own freshness window on every extension re-post, so a credential
        could stay "recently verified" indefinitely without anyone re-checking
        it — the failure this whole path was tightened to prevent, arriving one
        indirection later.
        """
        from openbiliclaw.api.source_auth.probe_cache import LIVE_PROBES
        from openbiliclaw.api.source_auth.verify import note_credential_changed
        from openbiliclaw.api.source_auth.write import credential_fingerprint

        if verdict.checked == "live_probe" and not verdict.from_cache:
            recorded = LIVE_PROBES.record(
                slug,
                authenticated=verdict.authenticated,
                detail=verdict.message,
                network_error=False,
                fingerprint=credential_fingerprint(slug, value),
                username=verdict.username,
                user_id=verdict.user_id,
            )
            if (
                slug == "v2ex"
                and recorded.authenticated
                and recorded.username
                and recorded.credential_fingerprint
                and hasattr(ctx.database, "set_v2ex_pat_identity")
            ):
                ctx.database.set_v2ex_pat_identity(
                    recorded.username,
                    credential_fingerprint=recorded.credential_fingerprint,
                )
        if changed:
            note_credential_changed(slug)

    async def _write_source_credential(
        slug: str,
        *,
        kind: str = "cookie",
        value: Any = "",
        source: str = "settings",
    ) -> CredentialWriteOutcome:
        """Validate → persist → broadcast → re-read. The one credential write.

        Every credential-bearing route funnels through here. Whether a route
        writes a credential is decided by what it stores, never by what its path
        spells (invariant I7) — reading the path is how ``PUT /api/config``, the
        single largest credential writer in the codebase, went unnoticed long
        enough to skip validation entirely (spec D4/D5). That route delegates to
        the same :func:`validate_credential` gate; it keeps its own persistence
        only because it is mid-transaction on ``config.toml`` and must not have
        its pending edits flushed out from under it.

        The returned ``auth`` is recomputed *after* the write, so the save and
        the next status poll cannot disagree. For the live-probe platforms the
        gate has already recorded a fresh verdict, so this costs no second round
        trip — the receipt is free, which is why there is no longer any excuse
        for the silent save that spec D7 complained about.
        """
        from openbiliclaw.api.source_auth.write import (
            CredentialWriteOutcome,
            cookie_names,
            current_credential,
            persist_credential,
            validate_credential,
        )
        from openbiliclaw.config import load_config

        cfg = _pin_active_runtime_config(load_config())
        verdict = await validate_credential(slug, kind, value, cfg=cfg)
        if not verdict.ok:
            return CredentialWriteOutcome(
                slug=slug,
                accepted=False,
                error_code=verdict.error_code,
                message=verdict.message,
                checked=verdict.checked,
                unverified_reason=verdict.unverified_reason,
                # Reported even on refusal: "which names did you actually see"
                # is the first question a user asks when a paste is rejected,
                # and it is derived from the payload, never from the store.
                cookie_names=(cookie_names(str(value or "")) if kind == "cookie" and value else ()),
                contract=_source_auth_contract(slug),
            )

        payload: Any = value
        if kind == "token":
            payload = _xhs_token_urls(value)

        # An unchanged cookie is an accepted no-op: the extension re-posts the
        # same jar around every startup, and rewriting the stores would restart
        # producer loops for no behavioural gain.
        text = str(value or "").strip() if kind == "cookie" else ""
        unchanged = bool(text) and text == current_credential(slug, cfg=cfg)

        written = persist_credential(
            slug,
            kind,
            payload,
            cfg=cfg,
            database=ctx.database,
            source=source,
            token_sink=lambda urls: _backfill_xhs_tokens(ctx.database, urls),
        )

        # The candidate is now the stored credential, so its verdict finally
        # describes something real and may be recorded (the gate deliberately
        # withheld it while the value was only a candidate). A ``token`` write
        # is excluded from the change signal: 小红书's ``xsec_token`` is per-note
        # content access, not a login, so it cannot invalidate a login verdict.
        if written.persisted:
            _credential_landed(
                slug,
                verdict=verdict,
                value=text,
                changed=kind != "token" and not unchanged,
            )

        if slug == "twitter" and written.persisted:
            # A freshly synced valid cookie is the external re-login signal that
            # clears a missing/expired/blocked health block. Without it the
            # producer's is_ready() gate stays False forever, so discovery never
            # retries even though auth is now fixed.
            with suppress(Exception):
                from openbiliclaw.storage.x_health import XSourceHealthStore

                XSourceHealthStore(ctx.database).clear_relogin_block()

        runtime_refreshed = False
        if slug == "bilibili" and written.runtime_dirty:
            # Atomic by contract: a partial ``rebuild_from_config`` leaves the
            # old runtime intact, so a failure here costs a stale client, never
            # a broken one.
            with suppress(Exception):
                await _rebuild_runtime_with_lane_handoff(load_config())
                runtime_refreshed = True

        event_type = {
            "bilibili": "bilibili_cookie_synced",
            "douyin": "douyin_cookie_synced",
            "twitter": "x_cookie_synced",
            "reddit": "reddit_cookie_synced",
        }.get(slug, "")
        if event_type and written.persisted:
            event: dict[str, Any] = {"type": event_type, "source": source}
            if slug == "bilibili":
                event["username"] = verdict.username
                event["user_id"] = verdict.user_id
            else:
                event["cookie_names"] = list(written.cookie_names)
            if slug in {"twitter", "reddit"}:
                event["has_cookie"] = True
            with suppress(Exception):
                await ctx.event_hub.publish(event)

        return CredentialWriteOutcome(
            slug=slug,
            accepted=True,
            message=verdict.message,
            persisted=bool(written.persisted) and not unchanged,
            checked=verdict.checked,
            unverified_reason=verdict.unverified_reason,
            cookie_names=written.cookie_names,
            authenticated=verdict.authenticated,
            username=verdict.username,
            user_id=verdict.user_id,
            credential_file=written.credential_file,
            updated_at=written.updated_at,
            upgraded=written.upgraded,
            runtime_refreshed=runtime_refreshed,
            contract=_source_auth_contract(slug),
        )

    @app.post("/api/sources/{slug}/credential", response_model=SourceCredentialWriteResponse)
    async def write_source_credential(
        slug: str, payload: SourceCredentialWriteIn
    ) -> SourceCredentialWriteResponse:
        """Store one source's credential, then report what that credential is worth.

        The single write shape all six older endpoints now forward into. Always
        200 for a known slug: a rejected credential is a normal outcome the UI
        has to render, and turning it into a 4xx would leave the frontends
        parsing error bodies to find the message explaining what to fix.
        """
        from openbiliclaw.api.source_auth.contract import SourceAuthContract
        from openbiliclaw.api.source_auth.providers import SOURCE_AUTH_PROVIDERS

        if slug not in SOURCE_AUTH_PROVIDERS:
            raise HTTPException(status_code=404, detail=f"unknown source: {slug}")

        value: Any = payload.pairs if payload.kind == "token" else payload.value
        # No ``live=`` argument: this route always runs the strongest check the
        # platform allows. See ``SourceCredentialWriteIn`` for why the request
        # cannot ask for less.
        result = await _write_source_credential(
            slug,
            kind=payload.kind,
            value=value,
            source=payload.source,
        )
        return SourceCredentialWriteResponse(
            slug=result.slug,
            accepted=result.accepted,
            error_code=result.error_code,
            message=result.message,
            persisted=result.persisted,
            checked=result.checked,
            unverified_reason=result.unverified_reason,
            cookie_names=list(result.cookie_names),
            auth=result.contract or SourceAuthContract(),
        )

    def _mask_source_credential(value: str, *, reveal: bool) -> str:
        if reveal or not value:
            return value
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}{'*' * max(4, len(value) - 8)}{value[-4:]}"

    def _xhs_token_from_url(url: str) -> str:
        match = re.search(r"(?:[?&])xsec_token=([^&#]+)", str(url or ""))
        return match.group(1) if match else ""

    def _latest_xhs_token() -> str:
        if not hasattr(ctx.database, "conn"):
            return ""
        queries = (
            """
            SELECT content_url
            FROM discovery_candidates
            WHERE source_platform = 'xiaohongshu'
              AND content_url LIKE '%xsec_token=%'
            ORDER BY last_seen_at DESC, id DESC
            LIMIT 1
            """,
            """
            SELECT content_url
            FROM content_cache
            WHERE source_platform = 'xiaohongshu'
              AND content_url LIKE '%xsec_token=%'
            ORDER BY discovered_at DESC, bvid DESC
            LIMIT 1
            """,
        )
        for sql in queries:
            with suppress(Exception):
                row = ctx.database.conn.execute(sql).fetchone()
                if row:
                    url = row["content_url"] if hasattr(row, "keys") else row[0]
                    token = _xhs_token_from_url(str(url))
                    if token:
                        return token
        return ""

    @app.get("/api/sources/credentials", response_model=SourcesCredentialsResponse)
    def sources_credentials(reveal_keys: bool = False) -> SourcesCredentialsResponse:
        """Return masked local credential snapshots for settings pages.

        ``reveal_keys`` remains a no-op for older desktop/extension builds.
        Secrets are write-only: a same-origin settings read must not become a
        bulk credential export.
        """
        del reveal_keys
        from openbiliclaw.api.source_auth.forms import (
            build_credential_form,
            credential_summary,
        )
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config
        from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie
        from openbiliclaw.sources.reddit_tasks import rdt_credential_cookie_names

        cfg = _pin_active_runtime_config(load_config())
        srcs = cfg.sources

        bili_cookie = resolve_runtime_cookie(
            data_dir=cfg.data_path,
            configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
        )
        dy_cookie = resolve_douyin_cookie(
            data_dir=cfg.data_path,
            cookie_env=getattr(srcs.douyin, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE"),
        )
        tw_cookie = resolve_x_cookie(
            data_dir=cfg.data_path,
            cookie_env=getattr(srcs.twitter, "cookie_env", "OPENBILICLAW_X_COOKIE"),
        )
        xhs_token = _latest_xhs_token()
        reddit_cookie_names = rdt_credential_cookie_names()

        def item(
            slug: str, label: str, value: str, detail: str, *, secret: bool = True
        ) -> SourceCredentialItem:
            """One credential row, form descriptor included.

            Every platform goes through here — including the three that store
            nothing — so a source can never ship without a ``form``. That is
            what lets the frontends drop their per-platform branches: an
            absent descriptor would just move the special-casing back to them.

            ``secret=False`` is for values that are not credentials: Reddit
            shows the *names* of the cookies rdt-cli holds, and masking a list
            of names would hide the only thing that row exists to tell you.
            """
            available = bool(value.strip())
            form = build_credential_form(slug, cfg=cfg)
            return SourceCredentialItem(
                label=label,
                value=_mask_source_credential(value, reveal=not secret),
                available=available,
                detail=detail,
                form=form,
                summary=credential_summary(form, label=label, available=available, detail=detail),
            )

        def _bangumi_credential_detail(sources: Any) -> str:
            base = (
                "Bangumi 公开收藏走官方只读 API 无需凭据；如需自动识别当前用户或读取"
                "私密收藏，可在下方填写个人令牌（后端仅保存该令牌，不保存 Cookie）。"
            )
            token_set = bool(
                str(getattr(getattr(sources, "bangumi", None), "access_token", "") or "").strip()
            )
            if not token_set or not hasattr(ctx.database, "conn"):
                return base
            from openbiliclaw.runtime.bangumi_producer import _read_token_rejection

            try:
                rejected = _read_token_rejection(ctx.database) is not None
            except Exception:
                rejected = False
            if rejected:
                return (
                    "已配置的个人令牌已被 Bangumi 拒绝（可能过期），当前已降级为匿名公开发现；"
                    "请到 https://next.bgm.tv/demo/access-token 重新生成后在下方填写替换。"
                )
            return base

        def _bangumi_credential_item(sources: Any, config: Any) -> SourceCredentialItem:
            """Bangumi's credential row: no pasteable value, but a real form.

            The token is write-only through the config / init form, never shown
            back (privacy: it reads private collections), so ``value`` stays empty
            and ``available`` only reports whether one is configured. The form
            descriptor (``form_kind='none'`` + verify / 去获取令牌 actions) is what
            gives the settings page its 「测试连接」 button and login link without a
            paste box that would write nowhere.
            """
            token_set = bool(
                str(getattr(getattr(sources, "bangumi", None), "access_token", "") or "").strip()
            )
            detail = _bangumi_credential_detail(sources)
            form = build_credential_form("bangumi", cfg=config)
            return SourceCredentialItem(
                label="可选个人令牌",
                available=token_set,
                detail=detail,
                form=form,
                summary=credential_summary(
                    form, label="个人令牌", available=token_set, detail=detail
                ),
            )

        def _github_credential_item(sources: Any, config: Any) -> SourceCredentialItem:
            """GitHub's config-only PAT row without any secret echo."""

            github_cfg = getattr(sources, "github", None)
            token_set = bool(
                str(os.environ.get("OPENBILICLAW_GITHUB_TOKEN", "") or "").strip()
                or str(getattr(github_cfg, "access_token", "") or "").strip()
            )
            detail = (
                "GitHub 公开仓库发现无需凭据；可选 PAT 只用于 GET /user 身份确认和提高"
                "公开 API 限额。后端不读取 GITHUB_TOKEN / GH_TOKEN，也不访问私有仓库。"
            )
            form = build_credential_form("github", cfg=config)
            return SourceCredentialItem(
                label="可选 PAT",
                # Never return the token, not even a masked prefix/suffix.
                value="",
                available=token_set,
                detail=detail,
                form=form,
                summary=credential_summary(
                    form,
                    label="PAT",
                    available=token_set,
                    detail=detail,
                ),
            )

        return SourcesCredentialsResponse(
            bilibili=item("bilibili", "Cookie", bili_cookie, "B 站当前 resolved Cookie。"),
            xiaohongshu=item(
                "xiaohongshu",
                "xsec_token",
                xhs_token,
                "小红书不保存整站 Cookie；xsec_token 只是内容访问令牌，不代表账号登录。",
            ),
            douyin=item("douyin", "Cookie", dy_cookie, "抖音当前 resolved Cookie。"),
            youtube=item(
                "youtube",
                "Cookie",
                "",
                "YouTube 当前按公开源接入，后端不保存 Cookie。",
            ),
            twitter=item("twitter", "Cookie", tw_cookie, "X 当前 resolved Cookie。"),
            zhihu=item(
                "zhihu",
                "Cookie",
                "",
                "知乎登录态保存在浏览器站点 / 插件上下文中，后端不保存可展示 Cookie。",
            ),
            reddit=item(
                "reddit",
                "rdt credential",
                ", ".join(reddit_cookie_names),
                "Reddit Cookie 由插件同步到 rdt-cli credential store；这里只展示 Cookie 名称，"
                "需要更换时可在下方 Reddit Cookie 覆盖输入框手动粘贴。",
                secret=False,
            ),
            bangumi=_bangumi_credential_item(srcs, cfg),
            github=_github_credential_item(srcs, cfg),
            linuxdo=item(
                "linuxdo",
                "浏览器登录态",
                "",
                "Linux.do 公开发现无需登录；个人收藏、点赞和阅读记录由浏览器插件"
                "同源读取，后端不保存 Cookie。",
            ),
            v2ex=item(
                "v2ex",
                "可选 PAT",
                str(
                    os.environ.get(getattr(srcs.v2ex, "token_env", "OPENBILICLAW_V2EX_TOKEN"), "")
                    or ""
                )
                or str(getattr(srcs.v2ex, "access_token", "") or ""),
                "V2EX 公开发现无需凭据；PAT 只用于 API 2.0 身份和增强读取，后端严格只读。",
            ),
            weibo=item(
                "weibo",
                "微博浏览器登录态",
                "",
                "微博公开发现无需登录；初始化本人收藏、关注和互动时，插件只同步布尔登录状态，"
                "实际只读请求在微博页面内执行，不读取或保存用户 Cookie。",
            ),
        )

    # ── Douyin task queue endpoints (extension dispatcher) ──────────
    # Independent from the XHS block above by design — see
    # docs/plans/2026-05-06-douyin-bootstrap-import-design.md
    # §"Module Isolation from XHS". Different table (dy_tasks),
    # different queue class, different fail isolation.

    from openbiliclaw.sources.dy_tasks import (
        DyTaskQueue,
        dy_bootstrap_video_key,
        dy_bootstrap_videos_to_events,
    )

    _dy_task_queue: DyTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _dy_task_queue = DyTaskQueue(ctx.database)

    @app.get("/api/sources/dy/next-task")
    def dy_next_task(response: Any = None) -> Any:
        """Return the oldest pending dy task, or 204 if none."""
        from starlette.responses import Response

        native_task = _claim_extension_native_task("dy")
        if native_task is not None:
            return native_task

        _cancel_disabled_source_incremental_tasks("dy")

        if _dy_task_queue is None:
            return Response(status_code=204)
        task = _dy_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/dy/task-result")
    async def dy_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a Douyin task result from the extension dispatcher.

        ``ok`` and ``degraded`` are terminal, while ``partial`` keeps the
        task pending and ``failed`` marks it failed. ``degraded`` means the
        extension preserved valid DOM/API items but could not prove the
        bootstrap was complete. The result schema uses ``videos`` instead of
        ``notes`` and propagation goes through ``dy_bootstrap_videos_to_events``.
        No self-author filtering yet
        (Douyin has its own posts in ``dy_post`` scope which we treat as
        a weak ``view`` signal — they're meant to count as input).
        """
        task_id = str(payload.get("task_id", "") or "").strip()
        if not task_id:
            raise HTTPException(status_code=422, detail="task_id is required")
        if _is_extension_native_job(task_id):
            if not _is_extension_native_job(task_id, "dy"):
                raise HTTPException(status_code=409, detail="task_result_conflict")
            return _submit_extension_native_result("dy", payload)

        status = str(payload.get("status", "") or "").strip()
        videos = [v for v in payload.get("videos", []) if isinstance(v, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        legacy_queue = _dy_task_queue
        if legacy_queue is None:
            raise HTTPException(status_code=409, detail="task_result_conflict")
        task = _require_legacy_task(legacy_queue, task_id)
        task_type = str(task.get("type", "")).strip() if task else ""
        if str(task.get("status", "")).strip() in {"completed", "failed"}:
            # Extension callbacks may race or be retried out of order.  Keep a
            # terminal result immutable and acknowledge the retry so the
            # dispatcher does not enter a resend loop.
            return {"ok": True, "ignored": True}

        from openbiliclaw.sources.task_result_protocol import (
            parse_task_result,
            staged_terminal_status,
        )

        canonical_result = parse_task_result(task.get("result_json"))
        staged_status = staged_terminal_status(canonical_result)

        if (
            staged_status
            or status in {"partial", "ok", "degraded"}
            or (status == "empty" and task_type == "bootstrap_profile")
        ):
            is_final = (
                bool(staged_status)
                or status in {"ok", "degraded"}
                or (status == "empty" and task_type == "bootstrap_profile")
            )
            if staged_status:
                # Repair exclusively from the first durable final payload.
                canonical_result = parse_task_result(task.get("result_json"))
            elif is_final:
                canonical_result = legacy_queue.stage_final_result(
                    task_id,
                    terminal_status=status,
                    videos=videos if videos else None,
                    scope_counts=scope_counts,
                    debug=debug,
                )
            else:
                legacy_queue.merge_result(
                    task_id,
                    videos=videos if videos else None,
                    scope_counts=scope_counts,
                    debug=debug,
                    complete=False,
                )
                canonical_task = legacy_queue.get(task_id) or {}
                canonical_result = parse_task_result(canonical_task.get("result_json"))
            canonical_videos = [
                value for value in canonical_result.get("videos", []) if isinstance(value, dict)
            ]
            # gui-init D1: persist the result (above) for init's own collector;
            # during init skip profile propagation for non-owned results, but
            # propagate init-OWNED bootstrap results through the deduped path.
            _init_busy = _init_active_now()
            _skip_profile = _init_busy and not _init_owns_task(task_id)
            if task_type == "bootstrap_profile" and canonical_videos and not _skip_profile:
                fresh_videos, video_keys_by_index = _filter_new_source_bootstrap_items(
                    "dy",
                    canonical_videos,
                    dy_bootstrap_video_key,
                )
                events_with_keys: list[tuple[dict[str, Any], str]] = []
                for index, video in enumerate(fresh_videos):
                    for event in dy_bootstrap_videos_to_events([video]):
                        key = video_keys_by_index.get(index, "")
                        events_with_keys.append((event, key))
                accepted_keys = await _accept_source_profile_events(
                    source="dy",
                    task_id=task_id,
                    events_with_keys=events_with_keys,
                    generic_owner=not _init_busy,
                )
                _mark_source_bootstrap_keys("dy", accepted_keys)
            if is_final:
                legacy_queue.complete_staged_result(task_id)
        else:
            legacy_queue.fail(task_id, error=payload.get("error", ""), debug=debug)

        return {"ok": True}

    # ── Wake-up kick endpoints ──────────────────────────────────────
    #
    # The extension's task dispatchers normally poll on a 60s
    # chrome.alarms timer. That's fine for the steady state but
    # introduces a 0–60s wait between CLI enqueue and extension pickup,
    # which racing init's 30s collect window is the actual reason init
    # sometimes prints "扩展未连接或任务仍在后台跑". These endpoints let
    # the CLI broadcast a wake-up event over the existing
    # /api/runtime-stream WebSocket so the dispatcher polls immediately
    # instead of waiting for the next alarm. The 60s alarm stays as
    # fallback for the WS-down case.

    @app.post("/api/sources/xhs/kick")
    async def xhs_task_kick() -> dict[str, Any]:
        """Broadcast `xhs_task_available` so any subscribed extension
        service-worker triggers an immediate poll. Idempotent and best
        effort — failures here never affect task state."""
        return await _kick_source_task("xhs")

    @app.post("/api/sources/dy/kick")
    async def dy_task_kick() -> dict[str, Any]:
        """Broadcast `dy_task_available` over runtime-stream. See
        xhs_task_kick docstring for rationale."""
        return await _kick_source_task("dy")

    @app.get("/api/sources/x/next-task")
    def x_next_task(response: Any = None) -> Any:
        """Return the oldest pending X native-save task, or 204 if none."""
        from starlette.responses import Response

        native_task = _claim_extension_native_task("x")
        return native_task if native_task is not None else Response(status_code=204)

    @app.post("/api/sources/x/task-result")
    async def x_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept an X native-save callback from the extension dispatcher."""
        task_id = str(payload.get("task_id", "") or "").strip()
        if not task_id:
            raise HTTPException(status_code=422, detail="task_id is required")
        if not _is_extension_native_job(task_id):
            raise HTTPException(status_code=409, detail="task_result_conflict")
        if not _is_extension_native_job(task_id, "x"):
            raise HTTPException(status_code=409, detail="task_result_conflict")
        return _submit_extension_native_result("x", payload)

    @app.post("/api/sources/x/kick")
    async def x_task_kick() -> dict[str, Any]:
        """Broadcast `x_task_available` over runtime-stream."""
        return await _kick_source_task("x")

    # ── YouTube bootstrap endpoints ────────────────────────────────
    from openbiliclaw.sources.linuxdo_tasks import (
        LinuxdoTaskQueue,
        LinuxdoTaskResultValidationError,
        linuxdo_bootstrap_item_key,
        linuxdo_bootstrap_items_to_events,
        validate_linuxdo_task_result,
    )
    from openbiliclaw.sources.reddit_tasks import (
        RedditTaskQueue,
        reddit_bootstrap_item_key,
        reddit_items_to_events,
    )
    from openbiliclaw.sources.v2ex_affinity import V2EXNodeAffinityStore
    from openbiliclaw.sources.v2ex_tasks import (
        V2EXFavoriteSnapshotStore,
        V2EXTaskQueue,
        v2ex_bootstrap_item_key,
        v2ex_bootstrap_items_to_events,
        v2ex_snapshot_effects_to_events,
    )
    from openbiliclaw.sources.weibo_tasks import (
        WeiboTaskQueue,
        weibo_bootstrap_item_key,
        weibo_bootstrap_items_to_events,
    )
    from openbiliclaw.sources.yt_tasks import (
        YtTaskQueue,
        yt_bootstrap_item_key,
        yt_bootstrap_items_to_events,
    )
    from openbiliclaw.sources.zhihu_tasks import (
        ZhihuTaskQueue,
        zhihu_bootstrap_item_key,
        zhihu_bootstrap_items_to_events,
    )

    _zhihu_task_queue: ZhihuTaskQueue | None = None
    _reddit_task_queue: RedditTaskQueue | None = None
    _linuxdo_task_queue: LinuxdoTaskQueue | None = None
    _v2ex_task_queue: V2EXTaskQueue | None = None
    _weibo_task_queue: WeiboTaskQueue | None = None
    _v2ex_snapshot_store: V2EXFavoriteSnapshotStore | None = None
    db_conn = getattr(ctx.database, "conn", None)
    if hasattr(db_conn, "executescript"):
        _zhihu_task_queue = ZhihuTaskQueue(ctx.database)
        _reddit_task_queue = RedditTaskQueue(ctx.database)
        _linuxdo_task_queue = LinuxdoTaskQueue(ctx.database)
        _v2ex_task_queue = V2EXTaskQueue(ctx.database)
        _weibo_task_queue = WeiboTaskQueue(ctx.database)
        _v2ex_snapshot_store = V2EXFavoriteSnapshotStore(ctx.database)

    @app.get("/api/sources/reddit/next-task")
    def reddit_next_task(response: Any = None) -> Any:
        """Return the oldest pending Reddit task, or 204 if none."""
        from starlette.responses import Response

        native_task = _claim_extension_native_task("reddit")
        if native_task is not None:
            return native_task

        _cancel_disabled_source_incremental_tasks("reddit")

        if _reddit_task_queue is None:
            return Response(status_code=204)
        task = _reddit_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/reddit/task-result")
    async def reddit_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a Reddit task result from the extension dispatcher.

        Plain Reddit fetch tasks only persist their canonical result. Bootstrap
        tasks marked by the backend as ``profile_update`` or ``incremental``
        additionally replay their accumulated rows through durable event
        ingress before the staged terminal status is flipped.
        """
        task_id = str(payload.get("task_id", "") or "").strip()
        if not task_id:
            raise HTTPException(status_code=422, detail="task_id is required")
        if _is_extension_native_job(task_id):
            if not _is_extension_native_job(task_id, "reddit"):
                raise HTTPException(status_code=409, detail="task_result_conflict")
            return _submit_extension_native_result("reddit", payload)

        status = str(payload.get("status", "") or "").strip()
        items = [v for v in payload.get("items", []) if isinstance(v, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        legacy_queue = _reddit_task_queue
        if legacy_queue is None:
            raise HTTPException(status_code=409, detail="task_result_conflict")
        task = _require_legacy_task(legacy_queue, task_id)
        task_type = str(task.get("type", "")).strip()
        if str(task.get("status", "")).strip() in {"completed", "failed"}:
            return {"ok": True, "ignored": True}

        task_payload: dict[str, Any] = {}
        if task.get("payload_json"):
            with suppress(Exception):
                parsed_payload = json.loads(str(task.get("payload_json") or "{}"))
                if isinstance(parsed_payload, dict):
                    task_payload = parsed_payload
        profile_update = bool(task_payload.get("profile_update"))
        incremental = bool(task_payload.get("incremental"))

        from openbiliclaw.sources.task_result_protocol import (
            parse_task_result,
            staged_terminal_status,
        )

        canonical_result = parse_task_result(task.get("result_json"))
        staged_status = staged_terminal_status(canonical_result)
        if staged_status or status in {"partial", "ok", "empty"}:
            is_final = bool(staged_status) or status in {"ok", "empty"}
            if staged_status:
                # Repair exclusively from the first durable final payload;
                # changed retry fields are intentionally ignored.
                canonical_result = parse_task_result(task.get("result_json"))
            elif is_final:
                canonical_result = legacy_queue.stage_final_result(
                    task_id,
                    terminal_status=status,
                    items=items if items else None,
                    scope_counts=scope_counts,
                    debug=debug,
                )
            else:
                legacy_queue.merge_result(
                    task_id,
                    items=items if items else None,
                    scope_counts=scope_counts,
                    debug=debug,
                    complete=False,
                )
                canonical_task = legacy_queue.get(task_id) or {}
                canonical_result = parse_task_result(canonical_task.get("result_json"))

            canonical_items = [
                value for value in canonical_result.get("items", []) if isinstance(value, dict)
            ]
            _init_busy = _init_active_now()
            _skip_profile = _init_busy and not _init_owns_task(task_id)
            if (
                task_type == "bootstrap_events"
                and (profile_update or incremental)
                and canonical_items
                and not _skip_profile
            ):
                fresh_items, item_keys_by_index = _filter_new_source_bootstrap_items(
                    "reddit",
                    canonical_items,
                    reddit_bootstrap_item_key,
                )
                events_with_keys: list[tuple[dict[str, Any], str]] = []
                for index, item in enumerate(fresh_items):
                    for event in reddit_items_to_events(
                        [item],
                        import_source="reddit_bootstrap_events",
                    ):
                        key = item_keys_by_index.get(index, "")
                        events_with_keys.append((event, key))
                accepted_keys = await _accept_source_profile_events(
                    source="reddit",
                    task_id=task_id,
                    events_with_keys=events_with_keys,
                    generic_owner=not _init_busy,
                )
                _mark_source_bootstrap_keys("reddit", accepted_keys)
            if is_final:
                legacy_queue.complete_staged_result(task_id)
        else:
            legacy_queue.fail(
                task_id,
                error=str(payload.get("error", "") or ""),
                debug=debug,
            )

        return {"ok": True}

    @app.post("/api/sources/reddit/kick")
    async def reddit_task_kick() -> dict[str, Any]:
        """Broadcast `reddit_task_available` over runtime-stream."""
        return await _kick_source_task("reddit")

    # ── V2EX browser-bootstrap endpoints ───────────────────────────

    @app.get("/api/sources/v2ex/next-task")
    def v2ex_next_task(response: Any = None) -> Any:
        """Return the oldest pending read-only V2EX browser task, or 204."""
        from starlette.responses import Response

        _cancel_disabled_source_incremental_tasks("v2ex")
        if _v2ex_task_queue is None:
            return Response(status_code=204)
        task = _v2ex_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)
        payload = json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **(payload if isinstance(payload, dict) else {}),
        }

    @app.post("/api/sources/v2ex/task-result")
    async def v2ex_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept and project one staged V2EX browser-bootstrap result.

        The extension returns only public DOM-derived rows. The first terminal
        callback is frozen in ``result_json`` before profile and Node-affinity
        projections run, so a retry after a process crash cannot replace the
        canonical result with a different browser payload.
        """
        task_id = str(payload.get("task_id", "") or "").strip()
        if not task_id:
            raise HTTPException(status_code=422, detail="task_id is required")
        if _is_extension_native_job(task_id):
            raise HTTPException(status_code=409, detail="task_result_conflict")

        queue = _v2ex_task_queue
        if queue is None:
            raise HTTPException(status_code=409, detail="task_result_conflict")
        task = _require_legacy_task(queue, task_id)
        if str(task.get("status", "")).strip() in {"completed", "failed"}:
            return {"ok": True, "ignored": True}

        status = str(payload.get("status", "") or "").strip().lower()
        items = [value for value in payload.get("items", []) if isinstance(value, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None
        if status == "failed":
            queue.fail(task_id, error=str(payload.get("error", "") or ""), debug=debug)
            return {"ok": True}

        task_payload: dict[str, Any] = {}
        if task.get("payload_json"):
            with suppress(Exception):
                parsed_payload = json.loads(str(task.get("payload_json") or "{}"))
                if isinstance(parsed_payload, dict):
                    task_payload = parsed_payload
        task_type = str(task.get("type", "")).strip()
        profile_update = bool(task_payload.get("profile_update"))
        incremental = bool(task_payload.get("incremental"))
        smoke_only = bool(task_payload.get("smoke_only"))
        profile_rebuild = bool(task_payload.get("profile_rebuild"))

        from openbiliclaw.sources.task_result_protocol import (
            parse_task_result,
            staged_terminal_status,
        )

        canonical_result = parse_task_result(task.get("result_json"))
        staged_status = staged_terminal_status(canonical_result)
        if staged_status:
            # The first final payload wins. Ignore changed retry fields.
            is_final = True
        elif status in {"ok", "partial", "empty"}:
            canonical_result = queue.stage_final_result(
                task_id,
                terminal_status=status,
                items=items if items else None,
                scope_counts=scope_counts,
                debug=debug,
            )
            is_final = True
        elif status:
            queue.merge_result(
                task_id,
                items=items if items else None,
                scope_counts=scope_counts,
                debug=debug,
            )
            canonical_task = queue.get(task_id) or {}
            canonical_result = parse_task_result(canonical_task.get("result_json"))
            is_final = False
        else:
            queue.fail(task_id, error="missing task status", debug=debug)
            return {"ok": True}

        canonical_items = [
            value for value in canonical_result.get("items", []) if isinstance(value, dict)
        ]
        canonical_debug = canonical_result.get("debug")
        canonical_debug = canonical_debug if isinstance(canonical_debug, dict) else {}
        if isinstance(canonical_debug.get("logged_in"), bool) and hasattr(
            ctx.database, "set_v2ex_login_state"
        ):
            ctx.database.set_v2ex_login_state(canonical_debug["logged_in"])
            if canonical_debug["logged_in"] is False and hasattr(
                ctx.database, "clear_v2ex_browser_identity"
            ):
                ctx.database.clear_v2ex_browser_identity()
        if canonical_debug.get("logged_in") is True:
            observed_username = str(canonical_debug.get("username", "") or "").strip()
            if observed_username and hasattr(ctx.database, "set_v2ex_browser_identity"):
                from openbiliclaw.sources.v2ex_client import validate_v2ex_username

                with suppress(ValueError):
                    ctx.database.set_v2ex_browser_identity(
                        validate_v2ex_username(observed_username),
                        evidence="observed",
                    )
        from openbiliclaw.api.source_auth.probe_cache import LIVE_PROBES
        from openbiliclaw.config import load_config
        from openbiliclaw.sources.v2ex_identity import resolve_v2ex_identity_state

        identity_resolution = resolve_v2ex_identity_state(
            cfg=load_config(),
            database=ctx.database,
            probes=LIVE_PROBES,
            configured_username=task_payload.get("username") or None,
            observed_username=canonical_debug.get("username"),
            observed_logged_in=(
                canonical_debug.get("logged_in")
                if isinstance(canonical_debug.get("logged_in"), bool)
                else None
            ),
        )
        init_busy = _init_active_now()
        skip_profile = init_busy and not _init_owns_task(task_id)
        active_profile_username = ""
        if hasattr(ctx.database, "get_v2ex_profile_identity"):
            active_profile_username = str(ctx.database.get_v2ex_profile_identity()[0] or "").strip()
        identity_switch_required = bool(
            active_profile_username
            and identity_resolution.username
            and active_profile_username.casefold() != identity_resolution.username.casefold()
        )
        init_owned_task = init_busy and _init_owns_task(task_id)
        switch_lane = init_owned_task or profile_rebuild
        # Every row in this protocol came from the signed-in browser.  A PAT or
        # manually accepted username is not enough to relabel another account's
        # DOM, and a steady-state incremental task may not switch the profile
        # owner behind the user's back.  Guided init is the sole switch lane:
        # stage 3 rebuilds the Soul from the new run before activation.
        identity_blocked = not identity_resolution.private_bootstrap_available or (
            identity_switch_required and not switch_lane
        )
        if (
            task_type == "bootstrap_profile"
            and not smoke_only
            and not skip_profile
            and not identity_blocked
        ):
            identity_prefix = f"identity:{identity_resolution.username.casefold()}:"

            def _identity_scoped_v2ex_key(item: dict[str, Any]) -> str:
                key = v2ex_bootstrap_item_key(item)
                return f"{identity_prefix}{key}" if key else ""

            if canonical_items:
                V2EXNodeAffinityStore(ctx.database).record_items(
                    canonical_items,
                    username=identity_resolution.username,
                )
            if (profile_update or incremental) and canonical_items:
                fresh_items, _item_keys_by_index = _filter_new_source_bootstrap_items(
                    "v2ex",
                    canonical_items,
                    _identity_scoped_v2ex_key,
                )
                events = v2ex_bootstrap_items_to_events(
                    fresh_items,
                    identity_username=identity_resolution.username,
                )
                events_with_keys: list[tuple[dict[str, Any], str]] = []
                for event in events:
                    metadata = event.get("metadata")
                    metadata = metadata if isinstance(metadata, dict) else {}
                    import_source = str(metadata.get("import_source") or "")
                    scope = import_source.removeprefix("v2ex_bootstrap_")
                    event_item: dict[str, Any] = {
                        "scope": scope,
                        "node_name": metadata.get("node_name", ""),
                        "topic_id": metadata.get("topic_id") or metadata.get("content_id", ""),
                    }
                    if scope == "favorite_nodes":
                        event_item.pop("topic_id", None)
                    key = v2ex_bootstrap_item_key(event_item)
                    if not key:
                        # Keep a URL fallback only for malformed but otherwise
                        # admissible rows; valid rows use the scope-aware key
                        # above. This is deliberately event-derived because
                        # Reply aggregation changes the raw item indexes.
                        key = v2ex_bootstrap_item_key(
                            {
                                "scope": scope,
                                "url": event.get("url", ""),
                                "title": event.get("title", ""),
                            }
                        )
                    if key:
                        key = f"{identity_prefix}{key}"
                    events_with_keys.append((event, key))
                accepted_keys = await _accept_source_profile_events(
                    source="v2ex",
                    task_id=task_id,
                    events_with_keys=events_with_keys,
                    generic_owner=not init_busy,
                )
                _mark_source_bootstrap_keys("v2ex", accepted_keys)
            # Seed the first complete bootstrap as the comparison baseline as
            # well as processing later incremental snapshots. Otherwise an
            # item removed between guided init and the first incremental run
            # would never enter the missing-streak ledger at all.
            if is_final and _v2ex_snapshot_store is not None:
                scope_complete = canonical_debug.get("scope_complete")
                scope_complete = scope_complete if isinstance(scope_complete, dict) else {}
                scope_statuses = canonical_debug.get("scope_statuses")
                scope_statuses = scope_statuses if isinstance(scope_statuses, dict) else {}
                snapshot_username = identity_resolution.username
                snapshot_effects: list[dict[str, Any]] = []
                if snapshot_username and canonical_debug.get("logged_in") is True:
                    for favorite_scope in ("favorite_topics", "favorite_nodes"):
                        if scope_complete.get(favorite_scope) is not True or scope_statuses.get(
                            favorite_scope
                        ) not in {"ok", "empty"}:
                            continue
                        snapshot_effects.extend(
                            _v2ex_snapshot_store.prepare_complete_snapshot(
                                task_id=task_id,
                                username=snapshot_username,
                                scope=favorite_scope,
                                items=canonical_items,
                            )
                        )
                if snapshot_effects:
                    snapshot_events = v2ex_snapshot_effects_to_events(
                        snapshot_effects,
                        identity_username=identity_resolution.username,
                    )
                    if len(snapshot_events) != len(snapshot_effects):
                        raise RuntimeError("V2EX snapshot effect conversion was incomplete")
                    snapshot_events_with_keys = [
                        (event, str(effect.get("effect_key") or ""))
                        for event, effect in zip(snapshot_events, snapshot_effects, strict=True)
                    ]
                    accepted_effect_keys = await _accept_source_profile_events(
                        source="v2ex",
                        task_id=task_id,
                        events_with_keys=snapshot_events_with_keys,
                        generic_owner=not init_busy,
                    )
                    if len(set(accepted_effect_keys)) != len(snapshot_effects):
                        raise RuntimeError("V2EX snapshot effects were not durably accepted")
                    V2EXNodeAffinityStore(ctx.database).apply_snapshot_effects(
                        snapshot_effects,
                        username=identity_resolution.username,
                    )
                    _v2ex_snapshot_store.mark_effects_emitted(accepted_effect_keys)
            if (
                not init_busy
                and not active_profile_username
                and not profile_rebuild
                and hasattr(ctx.database, "activate_v2ex_profile_identity")
            ):
                ctx.database.activate_v2ex_profile_identity(identity_resolution.username)
        if is_final:
            queue.complete_staged_result(task_id)
        if identity_blocked:
            return {
                "ok": True,
                "profile_paused": True,
                "identity_switch_required": identity_switch_required,
                "identity": identity_resolution.as_dict(),
            }
        return {"ok": True}

    @app.post("/api/sources/v2ex/kick")
    async def v2ex_task_kick() -> dict[str, Any]:
        """Broadcast ``v2ex_task_available`` to the extension dispatcher."""
        return await _kick_source_task("v2ex")

    @app.get("/api/sources/zhihu/next-task")
    def zhihu_next_task(response: Any = None) -> Any:
        """Return the oldest pending Zhihu task, or 204 if none."""
        from starlette.responses import Response

        native_task = _claim_extension_native_task("zhihu")
        if native_task is not None:
            return native_task

        _cancel_disabled_source_incremental_tasks("zhihu")

        zhihu_cfg = getattr(
            getattr(getattr(ctx, "config", None), "sources", None),
            "zhihu",
            None,
        )
        if not bool(getattr(zhihu_cfg, "enabled", False)):
            return Response(status_code=204)

        if _zhihu_task_queue is None:
            return Response(status_code=204)
        task = _zhihu_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/zhihu/task-result")
    async def zhihu_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a Zhihu task result from the extension dispatcher.

        Plain ``fetch-zhihu`` smoke tasks only record the task payload. Tasks
        explicitly marked ``profile_update`` or ``incremental`` also propagate
        bootstrap events to memory and, once a profile exists, into the
        incremental profile-update pipeline.
        """
        task_id = str(payload.get("task_id", "") or "").strip()
        if not task_id:
            raise HTTPException(status_code=422, detail="task_id is required")
        if _is_extension_native_job(task_id):
            if not _is_extension_native_job(task_id, "zhihu"):
                raise HTTPException(status_code=409, detail="task_result_conflict")
            return _submit_extension_native_result("zhihu", payload)

        status = str(payload.get("status", "") or "").strip()
        items = [v for v in payload.get("items", []) if isinstance(v, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        legacy_queue = _zhihu_task_queue
        if legacy_queue is None:
            raise HTTPException(status_code=409, detail="task_result_conflict")
        task = _require_legacy_task(legacy_queue, task_id)
        task_type = str(task.get("type", "")).strip() if task else ""
        if str(task.get("status", "")).strip() in {"completed", "failed"}:
            return {"ok": True, "ignored": True}
        task_payload: dict[str, Any] = {}
        if task and task.get("payload_json"):
            with suppress(Exception):
                parsed_payload = json.loads(str(task.get("payload_json") or "{}"))
                if isinstance(parsed_payload, dict):
                    task_payload = parsed_payload
        profile_update = bool(task_payload.get("profile_update"))
        incremental = bool(task_payload.get("incremental"))

        from openbiliclaw.sources.task_result_protocol import (
            parse_task_result,
            staged_terminal_status,
        )

        canonical_result = parse_task_result(task.get("result_json"))
        staged_status = staged_terminal_status(canonical_result)

        if staged_status or status in {"partial", "ok", "empty"}:
            is_final = bool(staged_status) or status in {"ok", "empty"}
            if staged_status:
                canonical_result = parse_task_result(task.get("result_json"))
            elif is_final:
                canonical_result = legacy_queue.stage_final_result(
                    task_id,
                    terminal_status=status,
                    items=items if items else None,
                    scope_counts=scope_counts,
                    debug=debug,
                )
            else:
                legacy_queue.merge_result(
                    task_id,
                    items=items if items else None,
                    scope_counts=scope_counts,
                    debug=debug,
                    complete=False,
                )
                canonical_task = legacy_queue.get(task_id) or {}
                canonical_result = parse_task_result(canonical_task.get("result_json"))
            canonical_items = [
                value for value in canonical_result.get("items", []) if isinstance(value, dict)
            ]
            _init_busy = _init_active_now()
            _skip_profile = _init_busy and not _init_owns_task(task_id)
            if (
                task_type == "bootstrap_events"
                and (profile_update or incremental)
                and canonical_items
                and not _skip_profile
            ):
                fresh_items, item_keys_by_index = _filter_new_source_bootstrap_items(
                    "zhihu",
                    canonical_items,
                    zhihu_bootstrap_item_key,
                )
                events_with_keys: list[tuple[dict[str, Any], str]] = []
                for index, item in enumerate(fresh_items):
                    for event in zhihu_bootstrap_items_to_events([item]):
                        key = item_keys_by_index.get(index, "")
                        events_with_keys.append((event, key))
                accepted_keys = await _accept_source_profile_events(
                    source="zhihu",
                    task_id=task_id,
                    events_with_keys=events_with_keys,
                    generic_owner=not _init_busy,
                )
                _mark_source_bootstrap_keys("zhihu", accepted_keys)
            if is_final:
                legacy_queue.complete_staged_result(task_id)
        else:
            legacy_queue.fail(task_id, error=str(payload.get("error", "") or ""), debug=debug)

        return {"ok": True}

    @app.post("/api/sources/zhihu/kick")
    async def zhihu_task_kick() -> dict[str, Any]:
        """Broadcast `zhihu_task_available` over runtime-stream."""
        return await _kick_source_task("zhihu")

    @app.get("/api/sources/weibo/next-task")
    def weibo_next_task(response: Any = None) -> Any:
        """Return the oldest pending logged-in Weibo task, or 204."""
        from starlette.responses import Response

        if _weibo_task_queue is None:
            return Response(status_code=204)
        runtime_config = getattr(ctx, "config", None)
        if runtime_config is None:
            from openbiliclaw.config import load_config

            runtime_config = load_config()
        weibo_cfg = getattr(
            getattr(runtime_config, "sources", None),
            "weibo",
            None,
        )
        if not bool(getattr(weibo_cfg, "enabled", False)):
            return Response(status_code=204)
        task = _weibo_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)
        payload = json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            "claim_token": task.get("claim_token", ""),
            **payload,
        }

    @app.post("/api/sources/weibo/task-result")
    async def weibo_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Stage and ingest read-only Weibo account signals from the extension."""
        task_id = str(payload.get("task_id", "") or "").strip()
        if not task_id:
            raise HTTPException(status_code=422, detail="task_id is required")
        claim_token = str(payload.get("claim_token", "") or "").strip()
        status = str(payload.get("status", "") or "").strip()
        raw_items = payload.get("items", [])
        if not isinstance(raw_items, list) or any(
            not isinstance(value, dict) for value in raw_items
        ):
            raise HTTPException(status_code=422, detail="invalid_task_items")
        items = list(raw_items)
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = {}
        queue = _weibo_task_queue
        if queue is None:
            raise HTTPException(status_code=409, detail="task_result_conflict")
        task = _require_legacy_task(queue, task_id)
        if str(task.get("status", "") or "").strip() in {"completed", "failed"}:
            return {"ok": True, "ignored": True}
        task_claim_token = str(task.get("claim_token", "") or "").strip()
        if task_claim_token and not queue.claim_token_matches(task_id, claim_token):
            raise HTTPException(status_code=409, detail="task_claim_conflict")
        effective_claim_token = claim_token if task_claim_token else None
        task_payload: dict[str, Any] = {}
        with suppress(Exception):
            parsed_payload = json.loads(str(task.get("payload_json") or "{}"))
            if isinstance(parsed_payload, dict):
                task_payload = parsed_payload
        profile_update = bool(task_payload.get("profile_update"))
        incremental = bool(task_payload.get("incremental"))
        from openbiliclaw.sources.weibo_tasks import (
            is_weibo_account_key,
            weibo_account_key,
        )

        raw_user_id = str(debug.get("user_id") or "").strip()
        raw_account_key = str(debug.get("account_key") or "").strip()
        # The task tab proves a real uid. Convert it at the backend boundary
        # into the stable opaque partition key used by event metadata and the
        # durable seen-key projection; never persist a raw uid as an account
        # binding. Accept an already-canonical key for staged retries from a
        # newer extension, but reject arbitrary caller-supplied identities.
        if raw_user_id:
            account_key = weibo_account_key(raw_user_id)
        elif is_weibo_account_key(raw_account_key):
            account_key = raw_account_key
        else:
            account_key = ""
        if account_key:
            debug = dict(debug)
            debug["account_key"] = account_key
        if (
            str(task.get("type", "") or "").strip() == "bootstrap_events"
            and (profile_update or incremental)
            and not account_key
            and status in {"ok", "empty", "partial"}
        ):
            queue.fail(
                task_id,
                claim_token=effective_claim_token,
                error="weibo_identity_required",
                debug=debug,
            )
            raise HTTPException(status_code=409, detail="weibo_identity_required")
        if account_key:
            bound = str(_load_source_bootstrap_state().get("weibo_account_key", "") or "").strip()
            if bound and bound != account_key:
                raise HTTPException(status_code=409, detail="weibo_account_switch_requires_reset")

        from openbiliclaw.sources.task_result_protocol import (
            parse_task_result,
            staged_terminal_status,
        )

        canonical_result = parse_task_result(task.get("result_json"))
        staged_status = staged_terminal_status(canonical_result)
        if staged_status or status in {"partial", "ok", "empty"}:
            is_final = bool(staged_status) or status in {"ok", "empty"}
            if staged_status:
                canonical_result = parse_task_result(task.get("result_json"))
            elif is_final:
                canonical_result = queue.stage_final_result(
                    task_id,
                    terminal_status=status,
                    claim_token=effective_claim_token,
                    items=items if items else None,
                    scope_counts=scope_counts,
                    debug=debug,
                )
            else:
                queue.merge_result(
                    task_id,
                    claim_token=effective_claim_token,
                    items=items if items else None,
                    scope_counts=scope_counts,
                    debug=debug,
                )
                canonical_result = parse_task_result((queue.get(task_id) or {}).get("result_json"))
            canonical_items = [
                value for value in canonical_result.get("items", []) if isinstance(value, dict)
            ]
            init_busy = _init_active_now()
            skip_profile = init_busy and not _init_owns_task(task_id)
            if (
                str(task.get("type", "")).strip() == "bootstrap_events"
                and (profile_update or incremental)
                and canonical_items
                and not skip_profile
            ):
                fresh_items, item_keys_by_index = _filter_new_source_bootstrap_items(
                    "weibo",
                    canonical_items,
                    lambda item: weibo_bootstrap_item_key(item, account_key=account_key),
                )
                events_with_keys: list[tuple[dict[str, Any], str]] = []
                for index, item in enumerate(fresh_items):
                    for event in weibo_bootstrap_items_to_events([item], account_key=account_key):
                        events_with_keys.append((event, item_keys_by_index.get(index, "")))
                accepted_keys = await _accept_source_profile_events(
                    source="weibo",
                    task_id=task_id,
                    events_with_keys=events_with_keys,
                    generic_owner=not init_busy,
                )
                _mark_source_bootstrap_keys("weibo", accepted_keys, account_key=account_key)
            if account_key and staged_terminal_status(canonical_result) in {"ok", "empty"}:
                _mark_source_bootstrap_keys("weibo", [], account_key=account_key)
            if is_final:
                queue.complete_staged_result(task_id, claim_token=effective_claim_token)
        elif status == "failed":
            queue.fail(
                task_id,
                claim_token=effective_claim_token,
                error=str(payload.get("error", "") or ""),
                debug=debug,
            )
        else:
            raise HTTPException(status_code=422, detail="invalid_result_status")
        return {"ok": True}

    @app.post("/api/sources/weibo/kick")
    async def weibo_task_kick() -> dict[str, Any]:
        """Broadcast ``weibo_task_available`` over runtime-stream."""
        return await _kick_source_task("weibo")

    @app.get("/api/sources/linuxdo/next-task")
    def linuxdo_next_task(response: Any = None) -> Any:
        """Return the oldest pending Linux.do browser task, or 204 if none."""
        from starlette.responses import Response

        _cancel_disabled_source_incremental_tasks("linuxdo")
        if _linuxdo_task_queue is None:
            return Response(status_code=204)
        linuxdo_cfg = getattr(
            getattr(getattr(ctx, "config", None), "sources", None),
            "linuxdo",
            None,
        )
        if not bool(getattr(linuxdo_cfg, "enabled", False)):
            return Response(status_code=204)
        task = _linuxdo_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            "claim_token": task["claim_token"],
            **payload,
        }

    @app.post("/api/sources/linuxdo/task-result")
    async def linuxdo_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Persist Linux.do task rows and ingest authorized personal signals.

        Discovery tasks only feed their waiting producer. ``bootstrap_events``
        tasks explicitly marked ``profile_update`` or ``incremental`` also
        traverse the durable event-ingress and bounded seen-key path before the
        first staged terminal result becomes immutable.
        """
        task_id = str(payload.get("task_id", "") or "").strip()
        if not task_id:
            raise HTTPException(status_code=422, detail="task_id is required")
        claim_token = str(payload.get("claim_token", "") or "").strip()

        status = str(payload.get("status", "") or "").strip()
        raw_items = payload.get("items", [])
        if not isinstance(raw_items, list) or any(
            not isinstance(value, dict) for value in raw_items
        ):
            raise HTTPException(status_code=422, detail="invalid_task_items")
        items = list(raw_items)
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None
        account_key = str(payload.get("account_key", "") or "").strip()
        response_observed = payload.get("response_observed") is True
        raw_complete_scopes = payload.get("complete_scopes", [])
        if not isinstance(raw_complete_scopes, list) or any(
            not isinstance(scope, str) for scope in raw_complete_scopes
        ):
            raise HTTPException(status_code=422, detail="invalid_complete_scopes")
        complete_scopes = [
            str(scope).strip() for scope in raw_complete_scopes if str(scope).strip()
        ]
        raw_next_cursors = payload.get("next_cursors", {})
        if not isinstance(raw_next_cursors, dict):
            raise HTTPException(status_code=422, detail="invalid_next_cursors")
        next_cursors = dict(raw_next_cursors)
        result_error = str(payload.get("error", "") or "").strip()

        legacy_queue = _linuxdo_task_queue
        if legacy_queue is None:
            raise HTTPException(status_code=409, detail="task_result_conflict")
        task = _require_legacy_task(legacy_queue, task_id)
        if not legacy_queue.claim_token_matches(task_id, claim_token):
            raise HTTPException(status_code=409, detail="task_claim_conflict")
        task_type = str(task.get("type", "")).strip()
        if str(task.get("status", "")).strip() in {"completed", "failed"}:
            return {"ok": True, "ignored": True}

        task_payload: dict[str, Any] = {}
        if task.get("payload_json"):
            with suppress(Exception):
                parsed_payload = json.loads(str(task.get("payload_json") or "{}"))
                if isinstance(parsed_payload, dict):
                    task_payload = parsed_payload
        profile_update = bool(task_payload.get("profile_update"))
        incremental = bool(task_payload.get("incremental"))

        from openbiliclaw.sources.task_result_protocol import (
            parse_task_result,
            staged_terminal_status,
        )

        def _validate_linuxdo_canonical(canonical: dict[str, Any]) -> None:
            canonical_items = [
                value for value in canonical.get("items", []) if isinstance(value, dict)
            ]
            validate_linuxdo_task_result(
                task_type=task_type,
                task_payload=task_payload,
                status=status,
                items=canonical_items,
                scope_counts=(
                    canonical.get("scope_counts")
                    if isinstance(canonical.get("scope_counts"), dict)
                    else None
                ),
                account_key=str(canonical.get("account_key", "") or ""),
                response_observed=bool(canonical.get("response_observed")),
                complete_scopes=[
                    str(scope)
                    for scope in canonical.get("complete_scopes", [])
                    if isinstance(scope, str)
                ],
                next_cursors=(
                    canonical.get("next_cursors")
                    if isinstance(canonical.get("next_cursors"), dict)
                    else None
                ),
            )

        canonical_result = parse_task_result(task.get("result_json"))
        staged_status = staged_terminal_status(canonical_result)
        if (
            not staged_status
            and task_type == "bootstrap_events"
            and (profile_update or incremental)
            and account_key
        ):
            bound_account_key = str(
                _load_source_bootstrap_state().get("linuxdo_account_key", "") or ""
            ).strip()
            if bound_account_key and bound_account_key != account_key:
                raise HTTPException(
                    status_code=409,
                    detail="linuxdo_account_switch_requires_reset",
                )
        if not staged_status:
            try:
                prospective = legacy_queue.preview_result(
                    task_id,
                    items=items if items else None,
                    scope_counts=scope_counts,
                    debug=debug,
                    account_key=account_key,
                    response_observed=response_observed,
                    complete_scopes=complete_scopes,
                    next_cursors=next_cursors,
                    error=result_error,
                )
                _validate_linuxdo_canonical(prospective)
            except LinuxdoTaskResultValidationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if staged_status or status in {"partial", "ok", "empty", "degraded", "failed"}:
            is_final = bool(staged_status) or status in {"ok", "empty", "degraded", "failed"}
            if staged_status:
                canonical_result = parse_task_result(task.get("result_json"))
            elif is_final:
                try:
                    canonical_result = legacy_queue.stage_final_result(
                        task_id,
                        terminal_status=status,
                        items=items if items else None,
                        scope_counts=scope_counts,
                        debug=debug,
                        account_key=account_key,
                        response_observed=response_observed,
                        complete_scopes=complete_scopes,
                        next_cursors=next_cursors,
                        error=result_error,
                        expected_claim_token=claim_token,
                        validate=_validate_linuxdo_canonical,
                    )
                except PermissionError as exc:
                    raise HTTPException(status_code=409, detail="task_claim_conflict") from exc
                except LinuxdoTaskResultValidationError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
            else:
                try:
                    legacy_queue.merge_result(
                        task_id,
                        items=items if items else None,
                        scope_counts=scope_counts,
                        debug=debug,
                        account_key=account_key,
                        response_observed=response_observed,
                        complete_scopes=complete_scopes,
                        next_cursors=next_cursors,
                        complete=False,
                        expected_claim_token=claim_token,
                        validate=_validate_linuxdo_canonical,
                    )
                except PermissionError as exc:
                    raise HTTPException(status_code=409, detail="task_claim_conflict") from exc
                except LinuxdoTaskResultValidationError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                canonical_task = legacy_queue.get(task_id) or {}
                canonical_result = parse_task_result(canonical_task.get("result_json"))

            canonical_items = [
                value for value in canonical_result.get("items", []) if isinstance(value, dict)
            ]
            canonical_account_key = str(canonical_result.get("account_key", "") or "")
            init_busy = _init_active_now()
            skip_profile = init_busy and not _init_owns_task(task_id)
            if (
                task_type == "bootstrap_events"
                and (profile_update or incremental)
                and canonical_items
                and not skip_profile
            ):
                fresh_items, item_keys_by_index = _filter_new_source_bootstrap_items(
                    "linuxdo",
                    canonical_items,
                    lambda item: linuxdo_bootstrap_item_key(
                        item,
                        account_key=canonical_account_key,
                    ),
                )
                events_with_keys: list[tuple[dict[str, Any], str]] = []
                for index, item in enumerate(fresh_items):
                    for event in linuxdo_bootstrap_items_to_events(
                        [item],
                        account_key=canonical_account_key,
                    ):
                        events_with_keys.append((event, item_keys_by_index.get(index, "")))
                accepted_keys = await _accept_source_profile_events(
                    source="linuxdo",
                    task_id=task_id,
                    events_with_keys=events_with_keys,
                    generic_owner=not init_busy,
                )
                _mark_source_bootstrap_keys(
                    "linuxdo",
                    accepted_keys,
                    account_key=canonical_account_key,
                )
            if (
                task_type == "bootstrap_events"
                and (profile_update or incremental)
                and canonical_account_key
                and staged_terminal_status(canonical_result) in {"ok", "empty", "degraded"}
            ):
                _mark_source_bootstrap_keys(
                    "linuxdo",
                    [],
                    account_key=canonical_account_key,
                )
            if is_final:
                try:
                    legacy_queue.complete_staged_result(
                        task_id,
                        expected_claim_token=claim_token,
                    )
                except PermissionError as exc:
                    raise HTTPException(status_code=409, detail="task_claim_conflict") from exc
        else:
            raise HTTPException(status_code=422, detail="invalid_result_status")

        return {"ok": True}

    @app.post("/api/sources/linuxdo/kick")
    async def linuxdo_task_kick() -> dict[str, Any]:
        """Broadcast `linuxdo_task_available` over runtime-stream."""
        return await _kick_source_task("linuxdo")

    _yt_task_queue: YtTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _yt_task_queue = YtTaskQueue(ctx.database)

    @app.get("/api/sources/yt/next-task")
    def yt_next_task(response: Any = None) -> Any:
        """Return the oldest pending YouTube task, or 204 if none."""
        from starlette.responses import Response

        native_task = _claim_extension_native_task("yt")
        if native_task is not None:
            return native_task

        _cancel_disabled_source_incremental_tasks("yt")

        if _yt_task_queue is None:
            return Response(status_code=204)
        # Issue #178: recover YouTube tasks whose extension claim outlived the
        # MV3 service worker timeout. Failing the stale lease here (instead of
        # handing it back via next_pending's stale-reclaim path) keeps a dead
        # task from being re-claimed forever and blocking fresh work.
        with suppress(Exception):
            _yt_task_queue.expire_stale_in_progress(("bootstrap_profile",))
        task = _yt_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/yt/task-result")
    async def yt_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a YouTube task result from the extension dispatcher."""
        task_id = str(payload.get("task_id", "") or "").strip()
        if not task_id:
            raise HTTPException(status_code=422, detail="task_id is required")
        if _is_extension_native_job(task_id):
            if not _is_extension_native_job(task_id, "yt"):
                raise HTTPException(status_code=409, detail="task_result_conflict")
            return _submit_extension_native_result("yt", payload)

        status = str(payload.get("status", "") or "").strip()
        items = [v for v in payload.get("items", []) if isinstance(v, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        legacy_queue = _yt_task_queue
        if legacy_queue is None:
            raise HTTPException(status_code=409, detail="task_result_conflict")
        task = _require_legacy_task(legacy_queue, task_id)
        task_type = str(task.get("type", "")).strip() if task else ""
        if str(task.get("status", "")).strip() in {"completed", "failed"}:
            return {"ok": True, "ignored": True}

        from openbiliclaw.sources.task_result_protocol import (
            parse_task_result,
            staged_terminal_status,
        )

        canonical_result = parse_task_result(task.get("result_json"))
        staged_status = staged_terminal_status(canonical_result)

        if (
            staged_status
            or status in {"partial", "ok"}
            or (status == "empty" and task_type == "bootstrap_profile")
        ):
            is_final = (
                bool(staged_status)
                or status == "ok"
                or (status == "empty" and task_type == "bootstrap_profile")
            )
            if staged_status:
                canonical_result = parse_task_result(task.get("result_json"))
            elif is_final:
                canonical_result = legacy_queue.stage_final_result(
                    task_id,
                    terminal_status=status,
                    items=items if items else None,
                    scope_counts=scope_counts,
                    debug=debug,
                )
            else:
                legacy_queue.merge_result(
                    task_id,
                    items=items if items else None,
                    scope_counts=scope_counts,
                    debug=debug,
                    complete=False,
                )
                canonical_task = legacy_queue.get(task_id) or {}
                canonical_result = parse_task_result(canonical_task.get("result_json"))
            canonical_items = [
                value for value in canonical_result.get("items", []) if isinstance(value, dict)
            ]
            # gui-init D1: persist the result (above) for init's own collector;
            # during init skip profile propagation for non-owned results, but
            # propagate init-OWNED bootstrap results through the deduped path.
            _init_busy = _init_active_now()
            _skip_profile = _init_busy and not _init_owns_task(task_id)
            if task_type == "bootstrap_profile" and canonical_items and not _skip_profile:
                fresh_items, item_keys_by_index = _filter_new_source_bootstrap_items(
                    "yt",
                    canonical_items,
                    yt_bootstrap_item_key,
                )
                events_with_keys: list[tuple[dict[str, Any], str]] = []
                for index, item in enumerate(fresh_items):
                    for event in yt_bootstrap_items_to_events([item]):
                        key = item_keys_by_index.get(index, "")
                        events_with_keys.append((event, key))
                accepted_keys = await _accept_source_profile_events(
                    source="yt",
                    task_id=task_id,
                    events_with_keys=events_with_keys,
                    generic_owner=not _init_busy,
                )
                _mark_source_bootstrap_keys("yt", accepted_keys)
            if is_final:
                legacy_queue.complete_staged_result(task_id)
        else:
            legacy_queue.fail(task_id, error=payload.get("error", ""), debug=debug)

        return {"ok": True}

    @app.post("/api/sources/yt/kick")
    async def yt_task_kick() -> dict[str, Any]:
        """Broadcast `yt_task_available` over runtime-stream."""
        return await _kick_source_task("yt")

    @app.post("/api/extension/e2e/run", response_model=ExtensionE2ERunOut)
    async def extension_e2e_run(
        request: Request,
        payload: ExtensionE2ERunIn,
    ) -> ExtensionE2ERunOut:
        """Local-only control plane for extension E2E simulation runs."""
        if not _get_auth_gate().is_trusted_local(request):
            raise HTTPException(status_code=403, detail="local_only")

        registry = cast("dict[str, _ExtensionE2ERunState]", app.state.extension_e2e_runs)
        if registry:
            raise HTTPException(status_code=409, detail="e2e_run_in_progress")

        native_save_authorization = payload.native_save_authorization
        expected_actions = (
            {}
            if native_save_authorization is not None
            else _extension_e2e_actions_for_request(payload)
        )
        if native_save_authorization is not None:
            item_key = make_item_key(
                native_save_authorization.platform,
                native_save_authorization.content_id,
            )
            try:
                item, route = _saved_service().validate_native_save_selection(
                    native_save_authorization.action,
                    item_key,
                )
            except (AttributeError, ValueError):
                raise HTTPException(
                    status_code=422,
                    detail="native_save_authorization_not_saved_content",
                ) from None
            if not _native_save_e2e_membership_matches(
                native_save_authorization,
                item,
                route,
            ):
                raise HTTPException(
                    status_code=422,
                    detail="native_save_authorization_not_saved_content",
                )
        if not payload.allow_state_changing:
            blocked_actions = sorted(
                {
                    action
                    for actions in expected_actions.values()
                    for action in actions
                    if action in _E2E_STATE_CHANGING_ACTIONS
                }
            )
            if blocked_actions:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "allow_state_changing must be true for actions: "
                        + ", ".join(blocked_actions)
                    ),
                )

        run_id = f"e2e-{uuid.uuid4().hex}"
        token = secrets.token_urlsafe(32)
        after_event_id = _latest_e2e_event_id(ctx)
        state = _ExtensionE2ERunState(
            run_id=run_id,
            token=token,
            started_at=time.time(),
            after_event_id=after_event_id,
            expected_actions=expected_actions,
            event=asyncio.Event(),
            native_save_authorization=native_save_authorization,
        )
        registry[run_id] = state
        timed_out = False

        try:
            publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
            if not callable(publish):
                state.error = "extension_runtime_unavailable"
            else:
                runtime_event: dict[str, object] = {
                    "type": "extension_e2e_run",
                    "source": "api",
                    "run_id": run_id,
                    "token": token,
                    "platforms": list(expected_actions.keys()),
                    "actions": {
                        platform: list(actions) for platform, actions in expected_actions.items()
                    },
                    "allow_state_changing": payload.allow_state_changing,
                    "timeout_seconds": payload.timeout_seconds,
                }
                if native_save_authorization is not None:
                    native_save_callback_deadline_ms = (
                        int(time.time() * 1000) + payload.timeout_seconds * 1000
                    )
                    runtime_event["native_save_authorization"] = (
                        native_save_authorization.model_dump()
                    )
                    runtime_event["native_save_execution_deadline_ms"] = (
                        native_save_callback_deadline_ms - 1000
                    )
                    runtime_event["native_save_callback_deadline_ms"] = (
                        native_save_callback_deadline_ms
                    )
                delivered = await publish(runtime_event)
                if delivered is False:
                    state.error = "extension_runtime_unavailable"

            if not state.error:
                try:
                    await asyncio.wait_for(state.event.wait(), timeout=payload.timeout_seconds)
                except TimeoutError:
                    timed_out = True

            events = _query_e2e_events(ctx, after_event_id=after_event_id)
            return _build_extension_e2e_report(
                state,
                events,
                timed_out=timed_out,
                timeout_seconds=payload.timeout_seconds,
            )
        finally:
            registry.pop(run_id, None)

    @app.post("/api/extension/e2e/result")
    async def extension_e2e_result(
        request: Request,
        payload: ExtensionE2EResultIn,
    ) -> dict[str, object]:
        """Accept a signed callback from the extension E2E runner."""
        if not _get_auth_gate().is_trusted_local(request):
            raise HTTPException(status_code=403, detail="local_only")

        registry = cast("dict[str, _ExtensionE2ERunState]", app.state.extension_e2e_runs)
        state = registry.get(payload.run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="unknown run_id")
        if not secrets.compare_digest(state.token, payload.token):
            raise HTTPException(status_code=403, detail="bad token")

        authorization = getattr(state, "native_save_authorization", None)
        if authorization is not None:
            result = payload.native_save_result
            if result is None:
                raise HTTPException(status_code=409, detail="native_save_result_required")
            if (
                result.platform != authorization.platform
                or result.action != authorization.action
                or result.content_id != authorization.content_id
                or result.expected_target != authorization.expected_target
            ):
                raise HTTPException(status_code=409, detail="native_save_result_mismatch")
        elif payload.native_save_result is not None:
            raise HTTPException(status_code=409, detail="unexpected_native_save_result")

        state.extension_result = payload
        state.event.set()
        return {"ok": True, "run_id": payload.run_id}

    @app.post("/api/extension/reload")
    async def extension_reload() -> dict[str, Any]:
        """Dev-only: broadcast `extension_reload` so the connected
        service-worker calls chrome.runtime.reload() — picks up the
        latest /dist bundle without the user clicking the reload icon
        in chrome://extensions.

        Best-effort — silent when no event-hub is wired."""
        delivered = False
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                delivered = bool(await publish({"type": "extension_reload", "source": "dev"}))
        return {"ok": True, "delivered": delivered}

    def _autostart_status_out(
        request: Request,
        cfg: Any,
        *,
        reason_override: str | None = None,
        detail_override: str | None = None,
    ) -> AutostartStatusOut:
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart.guards import (
            active_env_managed_inputs,
            autostart_shadowed,
        )
        from openbiliclaw.runtime.ollama_supervisor import (
            effective_ollama_endpoint,
            is_loopback,
            ollama_required,
        )

        state = autostart.status()
        managed_env = active_env_managed_inputs(cfg)
        shadowed = autostart_shadowed(cfg.autostart.enabled)
        trusted_local = _get_auth_gate().is_trusted_local(request)
        requires_ollama = ollama_required(cfg)

        reason = "none"
        if not state.supported:
            reason = state.reason
        elif not trusted_local:
            reason = "local_only"
        elif managed_env:
            reason = "env_managed"
        elif shadowed:
            reason = "shadowed"
        if reason_override is not None:
            reason = reason_override

        detail = ""
        if not state.supported:
            detail = "当前运行环境不支持注册开机自启动。"
        elif not trusted_local:
            detail = "仅本机可信请求可以修改开机自启动。"
        elif managed_env:
            detail = "检测到环境变量配置，自启动登录会话可能缺失：" + ", ".join(managed_env)
        elif shadowed:
            detail = "config.local.toml 覆盖了 [autostart].enabled，config.toml 修改不会生效。"
        elif cfg.autostart.enabled and not state.registered:
            detail = "开机自启动配置已开启，但系统自启动项缺失。"
        elif cfg.autostart.enabled:
            detail = "开机自启动已开启。"
        elif state.registered:
            detail = "配置已关闭，但检测到系统自启动残留项；关闭开关可清理。"
        else:
            detail = "尚未开启开机自启动。"
        if detail_override is not None:
            detail = detail_override

        if requires_ollama:
            endpoint = effective_ollama_endpoint(cfg)
            if not is_loopback(endpoint):
                detail = (detail + " " if detail else "") + "Ollama 端点是远端地址，需自行管理。"

        return AutostartStatusOut(
            supported=state.supported,
            enabled=cfg.autostart.enabled,
            registered=state.registered,
            can_manage=trusted_local and state.supported and not managed_env and not shadowed,
            platform=state.platform,
            mechanism=state.mechanism,
            manage_ollama=cfg.autostart.manage_ollama,
            ollama_required=requires_ollama,
            reason=reason,
            detail=detail,
        )

    @app.get("/api/autostart-status", response_model=AutostartStatusOut)
    def autostart_status(request: Request) -> AutostartStatusOut:
        from openbiliclaw.config import load_config

        cfg = load_config()
        return _autostart_status_out(request, cfg)

    @app.post("/api/autostart/apply", response_model=AutostartStatusOut)
    async def autostart_apply(
        payload: AutostartApplyIn, request: Request
    ) -> AutostartStatusOut | JSONResponse:
        from openbiliclaw.config import _default_config_path as _cfg_path
        from openbiliclaw.config import load_config as _load
        from openbiliclaw.config import save_config as _save
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart.guards import active_env_managed_inputs

        cfg = _load()
        if not _get_auth_gate().is_trusted_local(request):
            body = _autostart_status_out(
                request,
                cfg,
                reason_override="local_only",
                detail_override="仅本机可信请求可以修改开机自启动。",
            )
            return JSONResponse(status_code=403, content=body.model_dump(mode="json"))

        current = autostart.status()
        if not current.supported:
            body = _autostart_status_out(
                request,
                cfg,
                reason_override=current.reason,
                detail_override="当前运行环境不支持注册开机自启动。",
            )
            return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

        managed = active_env_managed_inputs(cfg)
        if payload.enabled and managed:
            body = _autostart_status_out(
                request,
                cfg,
                reason_override="env_managed",
                detail_override="检测到环境变量配置，自启动登录会话可能缺失：" + ", ".join(managed),
            )
            return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

        async with _CONFIG_SAVE_LOCK:
            config_path = _cfg_path()
            config_existed = config_path.exists()
            backup_path = _snapshot_config_file(config_path)

            def _rollback_cfg() -> None:
                if backup_path is not None:
                    with suppress(Exception):
                        _restore_config_snapshot(backup_path, config_path)
                elif not config_existed:
                    with suppress(Exception):
                        config_path.unlink(missing_ok=True)

            cfg = _load()
            was_registered = autostart.status().registered

            if payload.enabled:
                cfg.autostart.enabled = True
                try:
                    _save(cfg, autostart_authoritative=True)
                except Exception:
                    _rollback_cfg()
                    logger.warning("autostart: enable save_config failed", exc_info=True)
                    body = _autostart_status_out(
                        request,
                        _load(),
                        reason_override="unavailable",
                        detail_override="保存配置失败，开机自启动未修改。",
                    )
                    return JSONResponse(status_code=503, content=body.model_dump(mode="json"))

                effective = _load()
                if effective.autostart.enabled is not True:
                    _rollback_cfg()
                    body = _autostart_status_out(
                        request,
                        _load(),
                        reason_override="shadowed",
                        detail_override=(
                            "config.local.toml 覆盖了 [autostart].enabled，"
                            "config.toml 修改不会生效。"
                        ),
                    )
                    return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

                try:
                    autostart.register(effective)
                except Exception:
                    _rollback_cfg()
                    logger.warning("autostart: OS registration failed", exc_info=True)
                    body = _autostart_status_out(
                        request,
                        _load(),
                        reason_override="registration_failed",
                        detail_override="系统自启动项注册失败，配置已回滚。",
                    )
                    return JSONResponse(status_code=409, content=body.model_dump(mode="json"))
                return _autostart_status_out(request, _load())

            try:
                autostart.unregister()
            except Exception:
                logger.warning("autostart: OS unregister failed", exc_info=True)
                body = _autostart_status_out(
                    request,
                    cfg,
                    reason_override="unregister_failed",
                    detail_override="系统自启动项移除失败，配置未修改。",
                )
                return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

            cfg.autostart.enabled = False
            try:
                _save(cfg, autostart_authoritative=True)
            except Exception:
                if was_registered:
                    with suppress(Exception):
                        cfg.autostart.enabled = True
                        autostart.register(cfg)
                _rollback_cfg()
                logger.warning("autostart: disable save_config failed", exc_info=True)
                body = _autostart_status_out(
                    request,
                    _load(),
                    reason_override="unavailable",
                    detail_override="保存配置失败，系统自启动项已尝试恢复。",
                )
                return JSONResponse(status_code=503, content=body.model_dump(mode="json"))

            effective = _load()
            if effective.autostart.enabled is not False:
                if was_registered:
                    with suppress(Exception):
                        cfg.autostart.enabled = True
                        autostart.register(cfg)
                _rollback_cfg()
                body = _autostart_status_out(
                    request,
                    _load(),
                    reason_override="shadowed",
                    detail_override=(
                        "config.local.toml 覆盖了 [autostart].enabled，config.toml 修改不会生效。"
                    ),
                )
                return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

            return _autostart_status_out(request, _load())

    # ── Configuration management endpoints ──────────────────────────

    def _config_to_response(
        cfg: Any,
        issues: list[Any] | None = None,
        *,
        mask_keys: bool = True,
        degraded: bool = False,
        degraded_reason: str = "",
    ) -> ConfigResponse:
        """Convert a Config dataclass to a ConfigResponse, optionally masking API keys."""

        def _mask(key: str) -> str:
            if not mask_keys or not key:
                return key
            if len(key) <= 8:
                return "*" * len(key)
            return key[:4] + "*" * (len(key) - 8) + key[-4:]

        # Douyin / X store their cookie in data/*.json (env override wins),
        # not in config.toml — resolve here so the settings pages can show
        # the live credential exactly like the Bilibili card does.
        from openbiliclaw.config import (
            _normalize_pool_source_shares as _normalized_config_pool_source_shares,
        )
        from openbiliclaw.config import (
            effective_llm_default_chain,
            effective_llm_instances,
            effective_llm_routes,
        )
        from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie

        dy_cookie = ""
        with suppress(Exception):
            dy_cookie = resolve_douyin_cookie(
                data_dir=_active_runtime_data_path(),
                cookie_env=cfg.sources.douyin.cookie_env,
            )
        tw_cookie = ""
        with suppress(Exception):
            tw_cookie = resolve_x_cookie(
                data_dir=_active_runtime_data_path(),
                cookie_env=cfg.sources.twitter.cookie_env,
            )

        def _provider_out(p: Any) -> LLMProviderConfigOut:
            return LLMProviderConfigOut(
                api_key=_mask(p.api_key),
                model=p.model,
                base_url=p.base_url,
                auth_mode=getattr(p, "auth_mode", ""),
                api_flavor=getattr(p, "api_flavor", ""),
                http_referer=getattr(p, "http_referer", ""),
                x_title=getattr(p, "x_title", ""),
                reasoning_effort=getattr(p, "reasoning_effort", ""),
                num_ctx=int(getattr(p, "num_ctx", 0) or 0),
            )

        instances = effective_llm_instances(cfg.llm)
        default_chain = effective_llm_default_chain(cfg.llm)
        routes = effective_llm_routes(cfg.llm)

        def _instance_out(instance: Any) -> LLMInstanceConfigOut:
            return LLMInstanceConfigOut(
                **_provider_out(instance).model_dump(),
                name=str(getattr(instance, "name", "") or ""),
                provider_type=str(getattr(instance, "provider_type", "") or ""),
                enabled=bool(getattr(instance, "enabled", True)),
            )

        def _legacy_provider_projection(provider_type: str) -> Any:
            ordered_ids = [
                *default_chain,
                *[instance_id for instance_id in instances if instance_id not in default_chain],
            ]
            for instance_id in ordered_ids:
                instance = instances.get(instance_id)
                if (
                    instance is not None
                    and str(getattr(instance, "provider_type", "") or "").strip().lower()
                    == provider_type
                ):
                    return instance
            return getattr(cfg.llm, provider_type)

        def _source_date_pref_out_kwargs(source_cfg: Any) -> dict[str, Any]:
            return {
                "recommendation_date_preset": getattr(
                    source_cfg, "recommendation_date_preset", "all"
                ),
                "recommendation_date_start": getattr(source_cfg, "recommendation_date_start", ""),
                "recommendation_date_end": getattr(source_cfg, "recommendation_date_end", ""),
                "recommendation_date_weight": getattr(
                    source_cfg, "recommendation_date_weight", 0.5
                ),
            }

        def _legacy_module_out(bucket: str) -> ModuleLLMConfigOut:
            route = routes[bucket]
            provider = str(getattr(route, "provider", "") or "")
            model = str(getattr(route, "model", "") or "")
            if (
                bool(getattr(cfg.llm, "instance_routing", False))
                and not route.inherit
                and route.chain
            ):
                instance = instances.get(str(route.chain[0]).strip().lower())
                if instance is not None:
                    provider = str(getattr(instance, "provider_type", "") or "")
                    model = str(getattr(instance, "model", "") or "")
            return ModuleLLMConfigOut(
                provider=provider,
                model=model,
                inherit=bool(route.inherit),
                chain=list(route.chain),
            )

        first_instance = instances.get(default_chain[0]) if default_chain else None
        second_instance = instances.get(default_chain[1]) if len(default_chain) > 1 else None
        legacy_default_provider = (
            str(getattr(first_instance, "provider_type", "") or "")
            if first_instance is not None
            else str(cfg.llm.default_provider)
        )
        legacy_fallback_provider = (
            str(getattr(second_instance, "provider_type", "") or "")
            if second_instance is not None
            else str(cfg.llm.fallback_provider)
        )

        issue_list = [
            ConfigIssueOut(
                field=i.field,
                message=i.message,
                severity=getattr(i, "severity", "warning"),
            )
            for i in (issues or [])
        ]

        from openbiliclaw.runtime.tailnet_supervisor import (
            read_tailnet_status,
            tailnet_bootstrap_staged,
        )

        bootstrap_staged = tailnet_bootstrap_staged(cfg)
        tailnet_status = read_tailnet_status(cfg) or {}
        tailnet_ips_value = tailnet_status.get("ips", [])
        tailnet_ips = (
            [str(value) for value in tailnet_ips_value]
            if isinstance(tailnet_ips_value, list)
            else []
        )
        tailnet_port_value = tailnet_status.get("port", 0)
        tailnet_port = tailnet_port_value if isinstance(tailnet_port_value, int) else 0
        if not cfg.tailnet.enabled:
            tailnet_state = "disabled"
        elif bootstrap_staged:
            tailnet_state = "credential_staged"
        else:
            tailnet_state = str(tailnet_status.get("event") or "pending_restart")

        return ConfigResponse(
            language=cfg.language,
            data_dir=cfg.data_dir,
            degraded=degraded,
            degraded_reason=degraded_reason,
            llm=LLMConfigOut(
                routing_version=2,
                instances={
                    instance_id: _instance_out(instance)
                    for instance_id, instance in instances.items()
                },
                default_chain=default_chain,
                routes={
                    bucket: _legacy_module_out(bucket)
                    for bucket in ("soul", "discovery", "recommendation", "evaluation")
                },
                default_provider=legacy_default_provider,
                concurrency=int(getattr(cfg.llm, "concurrency", 3)),
                timeout=int(getattr(cfg.llm, "timeout", 1200)),
                fallback_provider=legacy_fallback_provider,
                openai=_provider_out(_legacy_provider_projection("openai")),
                claude=_provider_out(_legacy_provider_projection("claude")),
                gemini=_provider_out(_legacy_provider_projection("gemini")),
                deepseek=_provider_out(_legacy_provider_projection("deepseek")),
                ollama=_provider_out(_legacy_provider_projection("ollama")),
                openrouter=_provider_out(_legacy_provider_projection("openrouter")),
                openai_compatible=_provider_out(_legacy_provider_projection("openai_compatible")),
                orcarouter=_provider_out(_legacy_provider_projection("orcarouter")),
                embedding=EmbeddingConfigOut(
                    provider=cfg.llm.embedding.provider,
                    model=cfg.llm.embedding.model,
                    api_key=_mask(cfg.llm.embedding.api_key),
                    base_url=cfg.llm.embedding.base_url,
                    output_dimensionality=cfg.llm.embedding.output_dimensionality,
                    similarity_threshold=cfg.llm.embedding.similarity_threshold,
                    fallback_enabled=cfg.llm.embedding.fallback_enabled,
                    fallback_provider=cfg.llm.embedding.fallback_provider,
                    multimodal_enabled=cfg.llm.embedding.multimodal_enabled,
                ),
                soul=_legacy_module_out("soul"),
                discovery=_legacy_module_out("discovery"),
                recommendation=_legacy_module_out("recommendation"),
                evaluation=_legacy_module_out("evaluation"),
            ),
            bilibili=BilibiliConfigOut(
                auth_method=cfg.bilibili.auth_method,
                cookie=_mask(cfg.bilibili.cookie),
                browser_executable=cfg.bilibili.browser_executable,
                browser_headed=cfg.bilibili.browser_headed,
            ),
            network=NetworkConfigOut(
                mode=cfg.network.mode,
                proxy=_mask_proxy_userinfo(cfg.network.proxy) if mask_keys else cfg.network.proxy,
            ),
            sources=SourcesConfigOut(
                browser=SourcesBrowserConfigOut(
                    cdp_url=cfg.sources.browser_cdp_url,
                    headed=cfg.sources.browser_headed,
                ),
                bilibili=BilibiliSourceConfigOut(
                    enabled=cfg.sources.bilibili.enabled,
                    min_interval_minutes=cfg.sources.bilibili.min_interval_minutes,
                    **_source_date_pref_out_kwargs(cfg.sources.bilibili),
                ),
                xiaohongshu=XiaohongshuSourceConfigOut(
                    enabled=cfg.sources.xiaohongshu.enabled,
                    incremental_enabled=cfg.sources.xiaohongshu.incremental_enabled,
                    daily_search_budget=cfg.sources.xiaohongshu.daily_search_budget,
                    daily_creator_budget=cfg.sources.xiaohongshu.daily_creator_budget,
                    task_interval_seconds=cfg.sources.xiaohongshu.task_interval_seconds,
                    min_interval_minutes=cfg.sources.xiaohongshu.min_interval_minutes,
                    **_source_date_pref_out_kwargs(cfg.sources.xiaohongshu),
                ),
                douyin=DouyinSourceConfigOut(
                    enabled=cfg.sources.douyin.enabled,
                    incremental_enabled=cfg.sources.douyin.incremental_enabled,
                    mode=cfg.sources.douyin.mode,
                    cookie=_mask(dy_cookie),
                    cookie_env=cfg.sources.douyin.cookie_env,
                    daily_search_budget=cfg.sources.douyin.daily_search_budget,
                    daily_hot_budget=cfg.sources.douyin.daily_hot_budget,
                    daily_feed_budget=cfg.sources.douyin.daily_feed_budget,
                    request_interval_seconds=cfg.sources.douyin.request_interval_seconds,
                    min_interval_minutes=cfg.sources.douyin.min_interval_minutes,
                    **_source_date_pref_out_kwargs(cfg.sources.douyin),
                ),
                youtube=YoutubeSourceConfigOut(
                    enabled=cfg.sources.youtube.enabled,
                    incremental_enabled=cfg.sources.youtube.incremental_enabled,
                    daily_search_budget=cfg.sources.youtube.daily_search_budget,
                    daily_trending_budget=cfg.sources.youtube.daily_trending_budget,
                    daily_channel_budget=cfg.sources.youtube.daily_channel_budget,
                    request_interval_seconds=cfg.sources.youtube.request_interval_seconds,
                    min_interval_minutes=cfg.sources.youtube.min_interval_minutes,
                    **_source_date_pref_out_kwargs(cfg.sources.youtube),
                ),
                twitter=TwitterSourceConfigOut(
                    enabled=cfg.sources.twitter.enabled,
                    mode=cfg.sources.twitter.mode,
                    cookie=_mask(tw_cookie),
                    cookie_env=cfg.sources.twitter.cookie_env,
                    daily_search_budget=cfg.sources.twitter.daily_search_budget,
                    daily_feed_budget=cfg.sources.twitter.daily_feed_budget,
                    daily_creator_budget=cfg.sources.twitter.daily_creator_budget,
                    request_interval_seconds=cfg.sources.twitter.request_interval_seconds,
                    min_interval_minutes=cfg.sources.twitter.min_interval_minutes,
                    **_source_date_pref_out_kwargs(cfg.sources.twitter),
                ),
                zhihu=ZhihuSourceConfigOut(
                    enabled=cfg.sources.zhihu.enabled,
                    incremental_enabled=cfg.sources.zhihu.incremental_enabled,
                    source_modes=list(cfg.sources.zhihu.source_modes),
                    daily_search_budget=cfg.sources.zhihu.daily_search_budget,
                    daily_hot_budget=cfg.sources.zhihu.daily_hot_budget,
                    daily_feed_budget=cfg.sources.zhihu.daily_feed_budget,
                    daily_creator_budget=cfg.sources.zhihu.daily_creator_budget,
                    daily_related_budget=cfg.sources.zhihu.daily_related_budget,
                    request_interval_seconds=cfg.sources.zhihu.request_interval_seconds,
                    min_interval_minutes=cfg.sources.zhihu.min_interval_minutes,
                    **_source_date_pref_out_kwargs(cfg.sources.zhihu),
                ),
                reddit=RedditSourceConfigOut(
                    enabled=cfg.sources.reddit.enabled,
                    incremental_enabled=cfg.sources.reddit.incremental_enabled,
                    backend=cfg.sources.reddit.backend,
                    source_modes=list(cfg.sources.reddit.source_modes),
                    daily_search_budget=cfg.sources.reddit.daily_search_budget,
                    daily_hot_budget=cfg.sources.reddit.daily_hot_budget,
                    daily_subreddit_budget=cfg.sources.reddit.daily_subreddit_budget,
                    daily_related_budget=cfg.sources.reddit.daily_related_budget,
                    request_interval_seconds=cfg.sources.reddit.request_interval_seconds,
                    min_interval_minutes=cfg.sources.reddit.min_interval_minutes,
                    **_source_date_pref_out_kwargs(cfg.sources.reddit),
                ),
                bangumi=BangumiSourceConfigOut(
                    enabled=cfg.sources.bangumi.enabled,
                    username=cfg.sources.bangumi.username,
                    access_token_set=bool(str(cfg.sources.bangumi.access_token or "").strip()),
                    subject_types=list(cfg.sources.bangumi.subject_types),
                    source_modes=list(cfg.sources.bangumi.source_modes),
                    daily_search_budget=cfg.sources.bangumi.daily_search_budget,
                    daily_ranked_budget=cfg.sources.bangumi.daily_ranked_budget,
                    daily_latest_budget=cfg.sources.bangumi.daily_latest_budget,
                    request_interval_seconds=cfg.sources.bangumi.request_interval_seconds,
                    min_interval_minutes=cfg.sources.bangumi.min_interval_minutes,
                    bootstrap_limit=cfg.sources.bangumi.bootstrap_limit,
                    **_source_date_pref_out_kwargs(cfg.sources.bangumi),
                ),
                github=GitHubSourceConfigOut(
                    enabled=cfg.sources.github.enabled,
                    username=cfg.sources.github.username,
                    access_token_set=bool(
                        str(os.environ.get("OPENBILICLAW_GITHUB_TOKEN", "") or "").strip()
                        or str(cfg.sources.github.access_token or "").strip()
                    ),
                    # This field is intentionally invariant. It documents the
                    # only env credential the runtime is allowed to read.
                    token_env="OPENBILICLAW_GITHUB_TOKEN",
                    source_modes=list(cfg.sources.github.source_modes),
                    daily_search_budget=cfg.sources.github.daily_search_budget,
                    daily_ranked_budget=cfg.sources.github.daily_ranked_budget,
                    daily_latest_budget=cfg.sources.github.daily_latest_budget,
                    request_interval_seconds=cfg.sources.github.request_interval_seconds,
                    min_interval_minutes=cfg.sources.github.min_interval_minutes,
                    bootstrap_limit=cfg.sources.github.bootstrap_limit,
                    bootstrap_max_pages=cfg.sources.github.bootstrap_max_pages,
                    **_source_date_pref_out_kwargs(cfg.sources.github),
                ),
                linuxdo=LinuxdoSourceConfigOut(
                    enabled=cfg.sources.linuxdo.enabled,
                    incremental_enabled=cfg.sources.linuxdo.incremental_enabled,
                    source_modes=list(cfg.sources.linuxdo.source_modes),
                    daily_search_budget=cfg.sources.linuxdo.daily_search_budget,
                    daily_hot_budget=cfg.sources.linuxdo.daily_hot_budget,
                    daily_feed_budget=cfg.sources.linuxdo.daily_feed_budget,
                    daily_creator_budget=cfg.sources.linuxdo.daily_creator_budget,
                    daily_related_budget=cfg.sources.linuxdo.daily_related_budget,
                    request_interval_seconds=cfg.sources.linuxdo.request_interval_seconds,
                    min_interval_minutes=cfg.sources.linuxdo.min_interval_minutes,
                    bootstrap_limit=cfg.sources.linuxdo.bootstrap_limit,
                    **_source_date_pref_out_kwargs(cfg.sources.linuxdo),
                ),
                v2ex=V2EXSourceConfigOut(
                    enabled=cfg.sources.v2ex.enabled,
                    incremental_enabled=cfg.sources.v2ex.incremental_enabled,
                    username=cfg.sources.v2ex.username,
                    access_token_set=bool(
                        str(os.environ.get(cfg.sources.v2ex.token_env, "") or "").strip()
                        or str(cfg.sources.v2ex.access_token or "").strip()
                    ),
                    token_env=cfg.sources.v2ex.token_env,
                    source_modes=list(cfg.sources.v2ex.source_modes),
                    tab_modes=list(cfg.sources.v2ex.tab_modes),
                    node_allowlist=list(cfg.sources.v2ex.node_allowlist),
                    node_blocklist=list(cfg.sources.v2ex.node_blocklist),
                    node_downweight=list(cfg.sources.v2ex.node_downweight),
                    daily_search_budget=cfg.sources.v2ex.daily_search_budget,
                    daily_node_budget=cfg.sources.v2ex.daily_node_budget,
                    daily_tab_budget=cfg.sources.v2ex.daily_tab_budget,
                    daily_hot_budget=cfg.sources.v2ex.daily_hot_budget,
                    daily_latest_budget=cfg.sources.v2ex.daily_latest_budget,
                    request_interval_seconds=cfg.sources.v2ex.request_interval_seconds,
                    min_interval_minutes=cfg.sources.v2ex.min_interval_minutes,
                    detail_fetch_limit=cfg.sources.v2ex.detail_fetch_limit,
                    reply_enrichment_limit=cfg.sources.v2ex.reply_enrichment_limit,
                    max_topic_chars=cfg.sources.v2ex.max_topic_chars,
                    max_reply_digest_chars=cfg.sources.v2ex.max_reply_digest_chars,
                    max_profile_nodes=cfg.sources.v2ex.max_profile_nodes,
                    bootstrap_topics_limit=cfg.sources.v2ex.bootstrap_topics_limit,
                    bootstrap_replies_limit=cfg.sources.v2ex.bootstrap_replies_limit,
                    bootstrap_favorites_limit=cfg.sources.v2ex.bootstrap_favorites_limit,
                    bootstrap_max_pages_per_scope=cfg.sources.v2ex.bootstrap_max_pages_per_scope,
                    **_source_date_pref_out_kwargs(cfg.sources.v2ex),
                ),
                weibo=WeiboSourceConfigOut(
                    enabled=cfg.sources.weibo.enabled,
                    source_modes=list(cfg.sources.weibo.source_modes),
                    daily_search_budget=cfg.sources.weibo.daily_search_budget,
                    daily_hot_budget=cfg.sources.weibo.daily_hot_budget,
                    daily_creator_budget=cfg.sources.weibo.daily_creator_budget,
                    request_interval_seconds=cfg.sources.weibo.request_interval_seconds,
                    min_interval_minutes=cfg.sources.weibo.min_interval_minutes,
                    **_source_date_pref_out_kwargs(cfg.sources.weibo),
                ),
            ),
            scheduler=SchedulerConfigOut(
                enabled=cfg.scheduler.enabled,
                llm_budget_max_calls=cfg.scheduler.llm_budget_max_calls,
                llm_budget_window_seconds=cfg.scheduler.llm_budget_window_seconds,
                pause_on_extension_disconnect=cfg.scheduler.pause_on_extension_disconnect,
                extension_disconnect_grace_seconds=cfg.scheduler.extension_disconnect_grace_seconds,
                discovery_cron=cfg.scheduler.discovery_cron,
                pool_target_count=cfg.scheduler.pool_target_count,
                copy_ready_target_count=cfg.scheduler.copy_ready_target_count,
                pool_source_shares=_normalized_config_pool_source_shares(
                    cfg.scheduler.pool_source_shares
                ),
                account_sync_interval_hours=cfg.scheduler.account_sync_interval_hours,
                source_incremental_enabled=getattr(
                    cfg.scheduler, "source_incremental_enabled", False
                ),
                source_incremental_hours=getattr(cfg.scheduler, "source_incremental_hours", 24),
                xhs_incremental_hours=getattr(cfg.scheduler, "xhs_incremental_hours", None),
                douyin_incremental_hours=getattr(cfg.scheduler, "douyin_incremental_hours", 0),
                youtube_incremental_hours=getattr(cfg.scheduler, "youtube_incremental_hours", None),
                zhihu_incremental_hours=getattr(cfg.scheduler, "zhihu_incremental_hours", None),
                reddit_incremental_hours=getattr(cfg.scheduler, "reddit_incremental_hours", None),
                linuxdo_incremental_hours=getattr(cfg.scheduler, "linuxdo_incremental_hours", None),
                v2ex_incremental_hours=getattr(cfg.scheduler, "v2ex_incremental_hours", None),
                refresh_check_interval_seconds=cfg.scheduler.refresh_check_interval_seconds,
                eval_min_batch_size=cfg.scheduler.eval_min_batch_size,
                eval_max_wait_seconds=cfg.scheduler.eval_max_wait_seconds,
                signal_event_threshold=cfg.scheduler.signal_event_threshold,
                feedback_batch_threshold=cfg.scheduler.feedback_batch_threshold,
                trending_refresh_minutes=cfg.scheduler.trending_refresh_minutes,
                explore_refresh_minutes=cfg.scheduler.explore_refresh_minutes,
                discovery_limit=cfg.scheduler.discovery_limit,
                delight_queue_limit=cfg.scheduler.delight_queue_limit,
                proactive_push_interval_seconds=cfg.scheduler.proactive_push_interval_seconds,
                speculator_idle_interval_minutes=cfg.scheduler.speculator_idle_interval_minutes,
                speculation_interval_minutes=cfg.scheduler.speculation_interval_minutes,
                speculation_ttl_days=cfg.scheduler.speculation_ttl_days,
                speculation_cooldown_days=cfg.scheduler.speculation_cooldown_days,
                speculation_confirmation_threshold=(
                    cfg.scheduler.speculation_confirmation_threshold
                ),
                speculation_max_active=cfg.scheduler.speculation_max_active,
                speculation_max_primary_interests=(cfg.scheduler.speculation_max_primary_interests),
                speculation_max_secondary_interests=(
                    cfg.scheduler.speculation_max_secondary_interests
                ),
                avoidance_speculation_interval_minutes=(
                    cfg.scheduler.avoidance_speculation_interval_minutes
                ),
                avoidance_speculation_ttl_days=cfg.scheduler.avoidance_speculation_ttl_days,
                avoidance_speculation_cooldown_days=(
                    cfg.scheduler.avoidance_speculation_cooldown_days
                ),
                avoidance_speculation_confirmation_threshold=(
                    cfg.scheduler.avoidance_speculation_confirmation_threshold
                ),
                avoidance_speculation_max_active=cfg.scheduler.avoidance_speculation_max_active,
                auto_update_enabled=cfg.scheduler.auto_update_enabled,
                auto_update_check_interval_hours=cfg.scheduler.auto_update_check_interval_hours,
                auto_update_allow_prerelease=cfg.scheduler.auto_update_allow_prerelease,
                auto_update_allowed_remotes=list(cfg.scheduler.auto_update_allowed_remotes),
            ),
            discovery=DiscoveryConfigOut(
                unified_keyword_planner_enabled=cfg.discovery.unified_keyword_planner_enabled,
                kw_cache_high=cfg.discovery.kw_cache_high,
                kw_cache_low=cfg.discovery.kw_cache_low,
                gen_batch=cfg.discovery.gen_batch,
                fetch_batch=cfg.discovery.fetch_batch,
                history_window_size=cfg.discovery.history_window_size,
                history_window_hours=cfg.discovery.history_window_hours,
                claim_lease_minutes=cfg.discovery.claim_lease_minutes,
                planner_poll_seconds=cfg.discovery.planner_poll_seconds,
                plan_ttl_hours=cfg.discovery.plan_ttl_hours,
                keyword_digest_grace_hours=cfg.discovery.keyword_digest_grace_hours,
                admission_min_score=cfg.discovery.admission_min_score,
                eval_prefilter_mode=cfg.discovery.eval_prefilter_mode,
                candidate_eval_concurrency=cfg.discovery.candidate_eval_concurrency,
                multimodal_evaluation_enabled=cfg.discovery.multimodal_evaluation_enabled,
                visual_profile_enabled=cfg.discovery.visual_profile_enabled,
                keyframe_enabled=cfg.discovery.keyframe_enabled,
                keyframe_max_frames=cfg.discovery.keyframe_max_frames,
                keyframe_fetch_limit=cfg.discovery.keyframe_fetch_limit,
                danmaku_enabled=cfg.discovery.danmaku_enabled,
                danmaku_fetch_limit=cfg.discovery.danmaku_fetch_limit,
                danmaku_max_chars=cfg.discovery.danmaku_max_chars,
                multimodal_batch_size=cfg.discovery.multimodal_batch_size,
                multimodal_image_max_px=cfg.discovery.multimodal_image_max_px,
                multimodal_image_quality=cfg.discovery.multimodal_image_quality,
                multimodal_image_timeout_seconds=(cfg.discovery.multimodal_image_timeout_seconds),
                keyword_generation_mode=_derive_keyword_generation_mode(
                    cfg.discovery.inspiration_search_enabled,
                    cfg.discovery.inspiration_replace_merged_keywords,
                ),
            ),
            autostart=AutostartConfigOut(
                enabled=cfg.autostart.enabled,
                manage_ollama=cfg.autostart.manage_ollama,
            ),
            saved_sync=SavedSyncConfigOut(
                auto_sync_enabled=cfg.saved_sync.auto_sync_enabled,
            ),
            tailnet=TailnetConfigOut(
                enabled=cfg.tailnet.enabled,
                hostname=cfg.tailnet.hostname,
                bootstrap_credential_staged=bootstrap_staged,
                state=tailnet_state,
                dns_name=str(tailnet_status.get("dns_name", "")),
                ips=tailnet_ips,
                port=tailnet_port,
            ),
            storage=StorageConfigOut(db_path=cfg.storage.db_path),
            logging=LoggingConfigOut(
                level=cfg.logging.level,
                file_level=cfg.logging.file_level,
                directory=cfg.logging.directory,
                filename=cfg.logging.filename,
                file_path=str(cfg.logging.file_path),
                max_file_size_mb=cfg.logging.max_file_size_mb,
                backup_count=cfg.logging.backup_count,
                aggregate_budget_mb=cfg.logging.aggregate_budget_mb,
                unmanaged_truncate_mb=cfg.logging.unmanaged_truncate_mb,
                unmanaged_max_age_days=cfg.logging.unmanaged_max_age_days,
            ),
            soul=SoulConfigOut(
                preference_prompt_view=cfg.soul.preference_prompt_view,
                awareness_prompt_view=cfg.soul.awareness_prompt_view,
                insight_prompt_view=cfg.soul.insight_prompt_view,
                posture_gate_mode=cfg.soul.posture_gate_mode,
                posture_gate_force_enforce=cfg.soul.posture_gate_force_enforce,
                topic_lifecycle_serialization=cfg.soul.topic_lifecycle_serialization,
                awareness_event_batch_size=int(cfg.soul.awareness_event_batch_size),
                insight_note_batch_size=int(cfg.soul.insight_note_batch_size),
                cognition_max_tokens=int(cfg.soul.cognition_max_tokens),
            ),
            issues=issue_list,
        )

    @app.get("/api/config", response_model=ConfigResponse)
    def get_config(reveal_keys: bool = False) -> ConfigResponse:
        """Return the current configuration with every secret masked.

        Older clients append ``?reveal_keys=true``. Keep accepting that query
        parameter so they continue to load, but never put raw API keys or
        credential-bearing proxy userinfo in a browser-readable response.
        Masked echoes are already treated as "keep existing" by PUT /api/config.
        """
        del reveal_keys
        from openbiliclaw.config import (
            _collect_config_issues,
            load_config,
        )

        cfg = load_config()
        issues = list(_collect_config_issues(cfg))
        if bool(getattr(ctx, "degraded", False)):
            issues.extend(getattr(ctx, "degraded_issues", []))
        return _config_to_response(
            cfg,
            issues,
            mask_keys=True,
            degraded=bool(getattr(ctx, "degraded", False)),
            degraded_reason=str(getattr(ctx, "degraded_reason", "")),
        )

    def _migration_request_allowed(request: Request) -> bool:
        """Require real loopback transport plus same-origin browser intent."""
        from openbiliclaw import auth_core

        gate = _get_auth_gate()
        if auth_core.is_extension_origin(request.headers.get("origin")):
            return False
        client_ip, local_transport = gate.resolve_client(request)
        return (
            request.headers.get("x-obc-auth") == "1"
            and auth_core.is_trusted_local(client_ip, local_transport)
            and gate._origin_safe_for_local(request)
        )

    def _tailnet_bootstrap_request_allowed(request: Request) -> bool:
        """Allow write-only enrollment secrets only from this PC's two settings UIs."""
        from openbiliclaw import auth_core

        gate = _get_auth_gate()
        client_ip, local_transport = gate.resolve_client(request)
        if not auth_core.is_trusted_local(client_ip, local_transport):
            return False
        origin = request.headers.get("origin")
        return auth_core.is_extension_origin(origin) or gate._origin_safe_for_local(request)

    def _require_local_migration_request(
        request: Request,
        *,
        allow_during_init: bool = False,
    ) -> None:
        if not _migration_request_allowed(request):
            raise HTTPException(status_code=403, detail={"code": "local_only"})
        if not allow_during_init and _init_active_now():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "init_active",
                    "message": "初始化进行中，完成或取消后再迁移数据。",
                },
            )

    async def _await_migration_worker(
        worker: asyncio.Task[Any],
        *,
        cleanup_result: Any = None,
    ) -> Any:
        """Do not abandon sensitive temp files or staging on request cancellation."""
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            try:
                result = worker.result()
            except BaseException:
                pass
            else:
                if callable(cleanup_result):
                    with suppress(Exception):
                        cleanup_result(result)
            raise

    @app.post("/api/migration/export")
    async def export_user_migration(
        request: Request,
        payload: Annotated[dict[str, Any] | None, Body()] = None,
    ) -> StreamingResponse:
        """Download a checksummed archive of portable user data and secrets."""
        _require_local_migration_request(request)
        if _CONFIG_SAVE_LOCK.locked():
            raise HTTPException(
                status_code=409,
                detail={"code": "config_busy", "message": "配置正在保存，请稍后再导出。"},
            )
        from openbiliclaw.config import load_config as load_migration_config
        from openbiliclaw.storage.migration import MigrationError, create_migration_archive

        frontend = (payload or {}).get("frontend")
        if frontend is not None and not isinstance(frontend, dict):
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_frontend", "message": "前端偏好格式无效。"},
            )
        if _MIGRATION_TRANSFER_LOCK.locked():
            raise HTTPException(
                status_code=409,
                detail={"code": "migration_busy", "message": "另一项迁移正在处理。"},
            )
        await _MIGRATION_TRANSFER_LOCK.acquire()
        exported: Any = None
        try:
            async with _CONFIG_SAVE_LOCK:
                current_config = await asyncio.to_thread(load_migration_config)
                current_config = _pin_active_runtime_config(current_config)
                worker = asyncio.create_task(
                    asyncio.to_thread(
                        create_migration_archive,
                        current_config,
                        frontend,
                    )
                )
                exported = await _await_migration_worker(
                    worker,
                    cleanup_result=lambda result: shutil.rmtree(
                        result.path.parent,
                        ignore_errors=True,
                    ),
                )
        except MigrationError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except (OSError, sqlite3.DatabaseError) as exc:
            logger.exception("Failed to create user-data migration archive")
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "export_failed",
                    "message": "创建迁移包失败，请检查磁盘空间。",
                },
            ) from exc
        finally:
            if exported is None:
                _MIGRATION_TRANSFER_LOCK.release()
        archive_path = exported.path
        try:
            archive_size = archive_path.stat().st_size
        except OSError as exc:
            shutil.rmtree(archive_path.parent, ignore_errors=True)
            _MIGRATION_TRANSFER_LOCK.release()
            raise HTTPException(
                status_code=500,
                detail={"code": "export_failed", "message": "迁移包生成后无法读取。"},
            ) from exc

        async def _stream_archive() -> AsyncIterator[bytes]:
            try:
                with archive_path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        yield chunk
            finally:
                shutil.rmtree(archive_path.parent, ignore_errors=True)

        try:
            return _MigrationArchiveStreamingResponse(
                _stream_archive(),
                media_type="application/vnd.openbiliclaw.backup+zip",
                cleanup_directory=archive_path.parent,
                release_callback=_MIGRATION_TRANSFER_LOCK.release,
                # A disconnect can happen before the body iterator is entered, so
                # its ``finally`` block alone cannot guarantee sensitive cleanup.
                # Both paths are idempotent and cover early as well as mid-stream
                # disconnects.
                background=BackgroundTask(
                    shutil.rmtree,
                    archive_path.parent,
                    ignore_errors=True,
                ),
                headers={
                    "Cache-Control": "no-store, private",
                    "Pragma": "no-cache",
                    "X-Content-Type-Options": "nosniff",
                    "X-OBC-Migration-Files": str(exported.file_count),
                    "Content-Length": str(archive_size),
                    "Content-Disposition": f'attachment; filename="{exported.filename}"',
                },
            )
        except Exception:
            shutil.rmtree(archive_path.parent, ignore_errors=True)
            _MIGRATION_TRANSFER_LOCK.release()
            raise

    @app.post("/api/migration/import", status_code=202)
    async def import_user_migration(request: Request) -> JSONResponse:
        """Validate an uploaded archive and stage replacement for next restart."""
        _require_local_migration_request(request)
        if request.headers.get("x-obc-migration-confirm") != "replace-all":
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "confirmation_required",
                    "message": "导入需要明确确认替换当前用户数据。",
                },
            )
        from openbiliclaw.config import load_config as load_migration_config
        from openbiliclaw.storage.migration import (
            MAX_MIGRATION_ARCHIVE_BYTES,
            MigrationError,
            stage_migration_archive,
        )

        raw_request_id = request.headers.get("x-obc-migration-request-id", "").strip()
        try:
            request_id = UUID(raw_request_id).hex if raw_request_id else uuid.uuid4().hex
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_request_id", "message": "迁移请求 ID 无效。"},
            ) from exc

        content_length = request.headers.get("content-length", "").strip()
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "invalid_content_length"},
                ) from exc
            if declared_size <= 0 or declared_size > MAX_MIGRATION_ARCHIVE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "archive_too_large",
                        "message": "迁移包为空或超过 2 GB。",
                    },
                )

        import tempfile
        from pathlib import Path as _MigrationPath

        if _MIGRATION_TRANSFER_LOCK.locked():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "migration_busy",
                    "message": "另一项迁移正在上传或校验，请稍后重试。",
                },
            )
        await _MIGRATION_TRANSFER_LOCK.acquire()
        app.state.migration_import_request_id = request_id
        app.state.migration_import_phase = "uploading"
        upload_dir: _MigrationPath | None = None
        try:
            upload_dir = _MigrationPath(tempfile.mkdtemp(prefix="openbiliclaw-import-upload-"))
            upload_path = upload_dir / "upload.obcbackup"
            received = 0
            with upload_path.open("xb") as handle:
                write_buffer = bytearray()
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > MAX_MIGRATION_ARCHIVE_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail={
                                "code": "archive_too_large",
                                "message": "迁移包超过 2 GB 上传上限。",
                            },
                        )
                    write_buffer.extend(chunk)
                    if len(write_buffer) >= 4 * 1024 * 1024:
                        await asyncio.to_thread(handle.write, write_buffer)
                        write_buffer.clear()
                if write_buffer:
                    await asyncio.to_thread(handle.write, write_buffer)
                await asyncio.to_thread(handle.flush)
                await asyncio.to_thread(os.fsync, handle.fileno())
            if received == 0:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "empty_archive", "message": "请选择迁移包文件。"},
                )
            if _CONFIG_SAVE_LOCK.locked():
                raise HTTPException(
                    status_code=409,
                    detail={"code": "config_busy", "message": "配置正在保存，请稍后再导入。"},
                )
            async with _CONFIG_SAVE_LOCK:
                current_config = await asyncio.to_thread(load_migration_config)
                app.state.migration_import_phase = "validating"
                worker = asyncio.create_task(
                    asyncio.to_thread(
                        stage_migration_archive,
                        upload_path,
                        current_config,
                        request_id=request_id,
                    )
                )
                staged = await _await_migration_worker(worker)
        except MigrationError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        finally:
            if upload_dir is not None:
                shutil.rmtree(upload_dir, ignore_errors=True)
            if app.state.migration_import_request_id == request_id:
                app.state.migration_import_request_id = ""
                app.state.migration_import_phase = ""
            _MIGRATION_TRANSFER_LOCK.release()

        return JSONResponse(
            status_code=202,
            content={
                "state": "staged",
                "migration_id": staged.migration_id,
                "request_id": staged.request_id,
                "source_version": staged.source_version,
                "file_count": staged.file_count,
                "uncompressed_bytes": staged.uncompressed_bytes,
                "frontend": staged.frontend_settings,
                "adjusted_fields": list(staged.adjusted_fields),
                "source_omitted_environment_variables": list(
                    staged.source_omitted_environment_variables
                ),
                "target_active_environment_variables": list(
                    staged.target_active_environment_variables
                ),
                "restart_required": True,
                "message": "迁移包已完整校验；请重启 OpenBiliClaw 以载入数据。",
            },
            headers={"Cache-Control": "no-store, private"},
        )

    @app.get("/api/migration/status")
    def get_user_migration_status(request: Request) -> JSONResponse:
        """Return pending/last restore status to the local settings page."""
        _require_local_migration_request(request, allow_during_init=True)
        from openbiliclaw.storage.migration import migration_status

        status = migration_status()
        active_request_id = str(app.state.migration_import_request_id or "")
        if active_request_id and not (
            status.get("state") == "staged"
            and str(status.get("request_id") or "") == active_request_id
        ):
            status = {
                "state": "processing",
                "request_id": active_request_id,
                "phase": str(app.state.migration_import_phase or "validating"),
                "restart_required": False,
                "message": "迁移包仍在上传或校验，当前数据尚未改动。",
            }
        return JSONResponse(
            status,
            headers={"Cache-Control": "no-store, private"},
        )

    @app.delete("/api/migration/pending")
    async def cancel_user_migration(request: Request) -> JSONResponse:
        """Cancel the staged restore while leaving current user data untouched."""
        _require_local_migration_request(request, allow_during_init=True)
        from openbiliclaw.storage.migration import MigrationError, cancel_pending_migration

        if _MIGRATION_TRANSFER_LOCK.locked():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "migration_busy",
                    "message": "迁移包仍在上传、导出或校验，请完成后再取消。",
                },
            )
        await _MIGRATION_TRANSFER_LOCK.acquire()
        try:
            async with _CONFIG_SAVE_LOCK:
                cancelled = await asyncio.to_thread(cancel_pending_migration)
        except MigrationError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        finally:
            _MIGRATION_TRANSFER_LOCK.release()
        return JSONResponse(
            {
                "state": "cancelled" if cancelled else "idle",
                "cancelled": cancelled,
                "restart_required": False,
                "message": (
                    "待导入迁移包已取消，当前数据未改动。"
                    if cancelled
                    else "当前没有待导入迁移包。"
                ),
            },
            headers={"Cache-Control": "no-store, private"},
        )

    @app.get("/api/config/apply-status", response_model=ConfigApplyStatusResponse)
    def get_config_apply_status() -> ConfigApplyStatusResponse:
        """Report whether the latest saved revision is queued, active, or applied."""
        return _config_apply_status_response()

    def _as_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _apply_llm_update(cfg: Any, llm_data: object) -> None:
        """Apply the LLM subset of a config update to an in-memory config."""
        if not isinstance(llm_data, dict):
            return
        from openbiliclaw.config import (
            _LLM_PROVIDER_DISPLAY_NAMES,
            LLMInstanceConfig,
            _normalize_llm_concurrency,
            _normalize_llm_timeout,
            effective_llm_instances,
            effective_llm_routes,
        )

        native_payload = any(
            key in llm_data for key in ("instances", "default_chain", "routes", "routing_version")
        )
        if native_payload:
            current_instances = effective_llm_instances(cfg.llm)
            current_routes = effective_llm_routes(cfg.llm)
            was_native = bool(getattr(cfg.llm, "instance_routing", False))
            cfg.llm.instance_routing = True
            if not was_native:
                for module_name, route in current_routes.items():
                    setattr(cfg.llm, module_name, route)
            if "instances" in llm_data:
                raw_instances = llm_data["instances"]
                if not isinstance(raw_instances, dict):
                    raise HTTPException(status_code=400, detail="llm.instances must be an object")
                replacement: dict[str, LLMInstanceConfig] = {}
                for raw_instance_id, raw_instance in raw_instances.items():
                    if not isinstance(raw_instance, dict):
                        raise HTTPException(
                            status_code=400,
                            detail=f"llm.instances.{raw_instance_id} must be an object",
                        )
                    instance_id = str(raw_instance_id).strip().lower()
                    if not instance_id:
                        raise HTTPException(
                            status_code=400,
                            detail="llm instance IDs cannot be empty",
                        )
                    if instance_id in replacement:
                        raise HTTPException(
                            status_code=400,
                            detail=f"duplicate normalized LLM instance ID: {instance_id}",
                        )
                    existing = current_instances.get(instance_id)
                    target = (
                        LLMInstanceConfig(
                            **{
                                field_name: getattr(existing, field_name)
                                for field_name in (
                                    "api_key",
                                    "model",
                                    "base_url",
                                    "auth_mode",
                                    "api_flavor",
                                    "http_referer",
                                    "x_title",
                                    "reasoning_effort",
                                    "num_ctx",
                                    "name",
                                    "provider_type",
                                    "enabled",
                                )
                            }
                        )
                        if existing is not None
                        else LLMInstanceConfig(name=instance_id)
                    )
                    for field_name in (
                        "name",
                        "provider_type",
                        "api_key",
                        "model",
                        "base_url",
                        "auth_mode",
                        "api_flavor",
                        "http_referer",
                        "x_title",
                        "reasoning_effort",
                    ):
                        if field_name not in raw_instance:
                            continue
                        new_value = str(raw_instance[field_name])
                        if field_name == "api_key" and "*" in new_value:
                            continue
                        if field_name in {"provider_type", "auth_mode", "api_flavor"}:
                            new_value = new_value.strip().lower()
                        setattr(target, field_name, new_value)
                    if "enabled" in raw_instance:
                        target.enabled = _as_bool(raw_instance["enabled"])
                    if "num_ctx" in raw_instance:
                        try:
                            target.num_ctx = int(raw_instance["num_ctx"] or 0)
                        except (TypeError, ValueError) as exc:
                            raise HTTPException(
                                status_code=400,
                                detail=f"llm.instances.{instance_id}.num_ctx must be an integer",
                            ) from exc
                    if (
                        existing is None
                        and "reasoning_effort" not in raw_instance
                        and target.provider_type == "openai_compatible"
                    ):
                        # The compatible adapter ignored its inherited
                        # "medium" default before explicit pass-through was
                        # supported. New clients send this field; old clients
                        # must retain the historical omit-on-wire behavior.
                        target.reasoning_effort = ""
                    replacement[instance_id] = target
                cfg.llm.instances = replacement
            if "default_chain" in llm_data:
                raw_chain = llm_data["default_chain"]
                if not isinstance(raw_chain, list):
                    raise HTTPException(
                        status_code=400,
                        detail="llm.default_chain must be an array",
                    )
                cfg.llm.default_chain = [
                    str(item).strip().lower() for item in raw_chain if str(item).strip()
                ]
            if "routes" in llm_data:
                raw_routes = llm_data["routes"]
                if not isinstance(raw_routes, dict):
                    raise HTTPException(status_code=400, detail="llm.routes must be an object")
                for module_name in ("soul", "discovery", "recommendation", "evaluation"):
                    if module_name not in raw_routes:
                        continue
                    route_data = raw_routes[module_name]
                    if not isinstance(route_data, dict):
                        raise HTTPException(
                            status_code=400,
                            detail=f"llm.routes.{module_name} must be an object",
                        )
                    route = getattr(cfg.llm, module_name)
                    if "inherit" in route_data:
                        route.inherit = _as_bool(route_data["inherit"])
                    if "chain" in route_data:
                        raw_route_chain = route_data["chain"]
                        if not isinstance(raw_route_chain, list):
                            raise HTTPException(
                                status_code=400,
                                detail=f"llm.routes.{module_name}.chain must be an array",
                            )
                        route.chain = [
                            str(item).strip().lower()
                            for item in raw_route_chain
                            if str(item).strip()
                        ]
                    route.provider = ""
                    route.model = ""

        def _native_instance_for_provider(
            provider_type: str,
            *,
            create: bool = False,
        ) -> tuple[str, Any] | tuple[str, None]:
            normalized_type = provider_type.strip().lower()
            ordered_ids = [
                *getattr(cfg.llm, "default_chain", []),
                *[
                    instance_id
                    for instance_id in getattr(cfg.llm, "instances", {})
                    if instance_id not in getattr(cfg.llm, "default_chain", [])
                ],
            ]
            for instance_id in ordered_ids:
                instance = cfg.llm.instances.get(instance_id)
                if (
                    instance is not None
                    and str(getattr(instance, "provider_type", "") or "").strip().lower()
                    == normalized_type
                ):
                    return instance_id, instance
            if not create or normalized_type not in {
                "openai",
                "claude",
                "gemini",
                "deepseek",
                "ollama",
                "openrouter",
                "orcarouter",
                "openai_compatible",
            }:
                return "", None
            instance_id = normalized_type.replace("_", "-")
            suffix = 2
            while instance_id in cfg.llm.instances:
                instance_id = f"{normalized_type.replace('_', '-')}-{suffix}"
                suffix += 1
            legacy = getattr(cfg.llm, normalized_type)
            instance = LLMInstanceConfig(
                api_key=legacy.api_key,
                model=legacy.model,
                base_url=legacy.base_url,
                auth_mode=legacy.auth_mode,
                api_flavor=legacy.api_flavor,
                http_referer=legacy.http_referer,
                x_title=legacy.x_title,
                reasoning_effort=legacy.reasoning_effort,
                num_ctx=legacy.num_ctx,
                name=_LLM_PROVIDER_DISPLAY_NAMES.get(normalized_type, normalized_type),
                provider_type=normalized_type,
                enabled=True,
            )
            cfg.llm.instances[instance_id] = instance
            return instance_id, instance

        if (
            bool(getattr(cfg.llm, "instance_routing", False))
            and not native_payload
            and "default_provider" in llm_data
        ):
            requested_type = str(llm_data["default_provider"]).strip().lower()
            instance_id, _instance = _native_instance_for_provider(requested_type, create=True)
            if instance_id:
                cfg.llm.default_chain = [
                    instance_id,
                    *[item for item in cfg.llm.default_chain if item != instance_id],
                ]
        elif "default_provider" in llm_data and not native_payload:
            cfg.llm.default_provider = str(llm_data["default_provider"])
        if "concurrency" in llm_data:
            cfg.llm.concurrency = _normalize_llm_concurrency(llm_data["concurrency"])
        if "timeout" in llm_data:
            cfg.llm.timeout = _normalize_llm_timeout(llm_data["timeout"])
        # Legacy clients (older extension popups) still send
        # "fallback_enabled" — deliberately ignored: a non-empty
        # fallback_provider is the only enable switch.
        if (
            bool(getattr(cfg.llm, "instance_routing", False))
            and not native_payload
            and "fallback_provider" in llm_data
        ):
            requested_type = str(llm_data["fallback_provider"]).strip().lower()
            primary = cfg.llm.default_chain[:1]
            remainder = cfg.llm.default_chain[1:]
            if requested_type:
                fallback_id, _instance = _native_instance_for_provider(
                    requested_type,
                    create=True,
                )
                remainder = [
                    fallback_id,
                    *[item for item in remainder if item != fallback_id],
                ]
            else:
                remainder = []
            cfg.llm.default_chain = [*primary, *remainder]
        elif "fallback_provider" in llm_data and not native_payload:
            cfg.llm.fallback_provider = str(llm_data["fallback_provider"]).strip()
        for provider_name in (
            "openai",
            "claude",
            "gemini",
            "deepseek",
            "ollama",
            "openrouter",
            "orcarouter",
            "openai_compatible",
        ):
            if provider_name in llm_data and isinstance(llm_data[provider_name], dict):
                if bool(getattr(cfg.llm, "instance_routing", False)) and not native_payload:
                    _instance_id, provider_cfg = _native_instance_for_provider(
                        provider_name,
                        create=True,
                    )
                else:
                    provider_cfg = getattr(cfg.llm, provider_name)
                if provider_cfg is None:
                    continue
                pdata = llm_data[provider_name]
                skipped_fields: list[str] = []
                for field_name in (
                    "api_key",
                    "model",
                    "base_url",
                    "auth_mode",
                    "api_flavor",
                    "http_referer",
                    "x_title",
                    "reasoning_effort",
                ):
                    if field_name in pdata:
                        new_value = str(pdata[field_name])
                        if field_name == "api_key" and "*" in new_value:
                            skipped_fields.append(f"{field_name}=masked")
                            continue
                        existing_value: object = getattr(provider_cfg, field_name, "")
                        if (
                            # These accept an explicit empty value ("" = back
                            # to default); the others treat empty as "keep".
                            field_name not in {"auth_mode", "reasoning_effort", "api_flavor"}
                            and not new_value.strip()
                            and isinstance(existing_value, str)
                            and existing_value.strip()
                        ):
                            skipped_fields.append(f"{field_name}=empty_skip")
                            continue
                        setattr(provider_cfg, field_name, new_value)
                if skipped_fields:
                    logger.debug(
                        "Config LLM update: provider %s skipped fields: %s",
                        provider_name,
                        ", ".join(skipped_fields),
                    )
        if "embedding" in llm_data and isinstance(llm_data["embedding"], dict):
            emb = llm_data["embedding"]
            if "provider" in emb:
                cfg.llm.embedding.provider = str(emb["provider"])
            if "model" in emb:
                new_model = str(emb["model"])
                if new_model.strip() or not cfg.llm.embedding.model.strip():
                    cfg.llm.embedding.model = new_model
            if "api_key" in emb:
                new_key = str(emb["api_key"])
                if "*" not in new_key and (
                    new_key.strip() or not cfg.llm.embedding.api_key.strip()
                ):
                    cfg.llm.embedding.api_key = new_key
            if "base_url" in emb:
                new_base_url = str(emb["base_url"])
                if new_base_url.strip() or not cfg.llm.embedding.base_url.strip():
                    cfg.llm.embedding.base_url = new_base_url
            if "output_dimensionality" in emb:
                try:
                    cfg.llm.embedding.output_dimensionality = max(
                        0,
                        int(emb["output_dimensionality"] or 0),
                    )
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=400,
                        detail="llm.embedding.output_dimensionality must be an integer",
                    ) from exc
            if "similarity_threshold" in emb:
                cfg.llm.embedding.similarity_threshold = float(emb["similarity_threshold"])
            if "fallback_enabled" in emb:
                cfg.llm.embedding.fallback_enabled = _as_bool(emb["fallback_enabled"])
            if "fallback_provider" in emb:
                cfg.llm.embedding.fallback_provider = str(emb["fallback_provider"]).strip()
            if "multimodal_enabled" in emb:
                cfg.llm.embedding.multimodal_enabled = _as_bool(emb["multimodal_enabled"])
        for module_name in ("soul", "discovery", "recommendation", "evaluation"):
            if module_name in llm_data and isinstance(llm_data[module_name], dict):
                mod_cfg = getattr(cfg.llm, module_name)
                mdata = llm_data[module_name]
                if bool(getattr(cfg.llm, "instance_routing", False)) and not native_payload:
                    provider_type = str(mdata.get("provider", "") or "").strip().lower()
                    model = str(mdata.get("model", "") or "").strip()
                    if not provider_type and not model:
                        mod_cfg.inherit = True
                        mod_cfg.chain = []
                        mod_cfg.provider = ""
                        mod_cfg.model = ""
                        continue
                    if not provider_type:
                        primary_id = cfg.llm.default_chain[0] if cfg.llm.default_chain else ""
                        primary = cfg.llm.instances.get(primary_id)
                        provider_type = (
                            str(getattr(primary, "provider_type", "") or "").strip().lower()
                        )
                    instance_id, instance = _native_instance_for_provider(
                        provider_type,
                        create=True,
                    )
                    if instance is None:
                        continue
                    if model and str(getattr(instance, "model", "") or "").strip() != model:
                        derived_id = f"legacy-{module_name}"
                        instance = LLMInstanceConfig(
                            api_key=instance.api_key,
                            model=model,
                            base_url=instance.base_url,
                            auth_mode=instance.auth_mode,
                            api_flavor=instance.api_flavor,
                            http_referer=instance.http_referer,
                            x_title=instance.x_title,
                            reasoning_effort=instance.reasoning_effort,
                            num_ctx=instance.num_ctx,
                            name=f"{module_name} · {instance.name}",
                            provider_type=instance.provider_type,
                            enabled=instance.enabled,
                        )
                        cfg.llm.instances[derived_id] = instance
                        instance_id = derived_id
                    mod_cfg.inherit = False
                    mod_cfg.chain = [instance_id]
                    mod_cfg.provider = ""
                    mod_cfg.model = ""
                    continue
                if "provider" in mdata:
                    mod_cfg.provider = str(mdata["provider"])
                if "model" in mdata:
                    mod_cfg.model = str(mdata["model"])

    def _reasoning_effort_advisory(provider: str, model: str) -> list[str]:
        """Return local UI suggestions without claiming protocol discovery.

        OpenAI-compatible ``GET /models`` standardizes identity metadata only;
        it has no portable endpoint for enumerating reasoning-effort values.
        Keep this ladder deliberately advisory and let the instance probe be
        the authority for a particular endpoint/model combination.
        """

        normalized_provider = provider.strip().lower()
        normalized_model = model.strip().lower()
        if normalized_provider == "openai":
            if normalized_model and not (
                normalized_model.startswith("gpt-5")
                or (
                    len(normalized_model) > 1
                    and normalized_model[0] == "o"
                    and normalized_model[1].isdigit()
                )
            ):
                return []
            return ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
        if normalized_provider in {"openai_compatible", "openrouter", "orcarouter"}:
            return ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
        if normalized_provider == "deepseek":
            return ["none", "high", "max"]
        if normalized_provider == "claude":
            return ["low", "medium", "high"]
        if normalized_provider == "gemini":
            return ["none", "minimal", "low", "medium", "high"]
        return []

    def _safe_model_discovery_error(exc: Exception, api_key: str) -> str:
        """Render a compact provider error without reflecting submitted keys."""

        message = " ".join(str(exc).split()) or type(exc).__name__
        secret = api_key.strip()
        if secret:
            message = message.replace(secret, "[REDACTED]")
        return message[:1000] + ("..." if len(message) > 1000 else "")

    async def _discover_llm_models(
        cfg: Any,
        *,
        instance_id: str,
    ) -> ConfigModelDiscoveryResponse:
        from openbiliclaw.config import effective_llm_instances
        from openbiliclaw.llm.openai_provider import OpenAIProvider
        from openbiliclaw.llm.registry import _build_instance_provider

        started = time.perf_counter()
        target_instance_id = str(instance_id or "").strip().lower()
        instances = effective_llm_instances(cfg.llm)
        instance = instances.get(target_instance_id)
        if not target_instance_id or instance is None:
            return ConfigModelDiscoveryResponse(
                ok=False,
                instance_id=target_instance_id,
                error="LLM instance was not found in the submitted configuration.",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        provider = str(getattr(instance, "provider_type", "") or "").strip().lower()
        model = str(getattr(instance, "model", "") or "").strip()
        api_key = str(getattr(instance, "api_key", "") or "")
        efforts = _reasoning_effort_advisory(provider, model)
        effort_source: Literal["local_advisory", "not_available"] = (
            "local_advisory" if efforts else "not_available"
        )
        if provider not in {
            "openai",
            "deepseek",
            "openrouter",
            "orcarouter",
            "ollama",
            "openai_compatible",
        }:
            return ConfigModelDiscoveryResponse(
                ok=False,
                instance_id=target_instance_id,
                provider=provider,
                reasoning_efforts=efforts,
                reasoning_efforts_source=effort_source,
                error=(
                    f"{provider or 'This provider'} does not expose the OpenAI-compatible "
                    "GET /models contract; keep or enter the model name manually."
                ),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        try:
            if provider == "ollama":
                base_url = (
                    str(getattr(instance, "base_url", "") or "").strip()
                    or "http://127.0.0.1:11434/v1"
                )
                if not base_url.rstrip("/").endswith("/v1"):
                    base_url = base_url.rstrip("/") + "/v1"
                provider_obj: Any = OpenAIProvider(
                    api_key=api_key or "ollama",
                    model=model or "__model_discovery__",
                    base_url=base_url,
                    provider_name="ollama",
                    timeout=20.0,
                    trust_env=False,
                )
            else:
                provider_obj = _build_instance_provider(cfg, provider, instance)
            list_models = getattr(provider_obj, "list_models", None)
            if not callable(list_models):
                raise RuntimeError(
                    "The submitted endpoint could not be constructed for model discovery."
                )
            models = await asyncio.wait_for(list_models(), timeout=20.0)
            return ConfigModelDiscoveryResponse(
                ok=True,
                instance_id=target_instance_id,
                provider=provider,
                models=models,
                reasoning_efforts=efforts,
                reasoning_efforts_source=effort_source,
                message=(
                    f"GET /models returned {len(models)} model(s); manual entry remains available."
                    if models
                    else "GET /models returned no models; manual entry remains available."
                ),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return ConfigModelDiscoveryResponse(
                ok=False,
                instance_id=target_instance_id,
                provider=provider,
                reasoning_efforts=efforts,
                reasoning_efforts_source=effort_source,
                error=_safe_model_discovery_error(exc, api_key),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

    async def _probe_llm_config(
        cfg: Any,
        *,
        kind: Literal["llm", "llm_instance", "llm_chain", "llm_fallback"] = "llm",
        instance_id: str = "",
    ) -> ConfigServiceProbeResponse:
        from openbiliclaw.config import effective_llm_default_chain
        from openbiliclaw.llm.base import LLM_CONNECTIVITY_PROBE_MAX_TOKENS
        from openbiliclaw.llm.registry import build_llm_registry

        started = time.perf_counter()
        is_fallback = kind == "llm_fallback"
        is_chain = kind == "llm_chain"
        is_instance = kind == "llm_instance"
        target_instance = str(instance_id or "").strip().lower()
        configured_chain = effective_llm_default_chain(cfg.llm)
        timeout_s = _config_llm_probe_timeout_seconds(
            getattr(cfg.llm, "timeout", 300),
        )
        if is_fallback:
            legacy_routing = not bool(getattr(cfg.llm, "instance_routing", False))
            legacy_default = str(getattr(cfg.llm, "default_provider", "") or "").strip().lower()
            legacy_fallback = str(getattr(cfg.llm, "fallback_provider", "") or "").strip().lower()
            target_instance = (
                legacy_fallback
                if legacy_routing
                else configured_chain[1]
                if len(configured_chain) > 1
                else ""
            )
            if not target_instance:
                return ConfigServiceProbeResponse(
                    ok=False,
                    kind=kind,
                    error="Fallback LLM provider is not configured.",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            if target_instance == (
                legacy_default
                if legacy_routing
                else configured_chain[0]
                if configured_chain
                else ""
            ):
                return ConfigServiceProbeResponse(
                    ok=False,
                    kind=kind,
                    instance_id=target_instance,
                    provider=target_instance,
                    error=(
                        f"Fallback provider {target_instance!r} is the same as the "
                        "default provider — it would never be used."
                    ),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
        elif not is_chain and not is_instance:
            target_instance = (
                configured_chain[0]
                if configured_chain
                else str(getattr(cfg.llm, "default_provider", "") or "").strip().lower()
            )
        provider = ""
        model = ""
        try:
            registry = build_llm_registry(cfg)
            if not is_chain and not is_instance and not is_fallback and not target_instance:
                target_instance = str(getattr(registry, "default_provider", "") or "")
            if is_instance and not target_instance:
                return ConfigServiceProbeResponse(
                    ok=False,
                    kind=kind,
                    error="LLM instance_id is required.",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            if not is_chain:
                if not registry.is_chat_capable(target_instance):
                    return ConfigServiceProbeResponse(
                        ok=False,
                        kind=kind,
                        instance_id=target_instance,
                        provider=target_instance,
                        model=model,
                        error=(
                            f"LLM instance {target_instance!r} is not registered "
                            "or not chat-capable."
                        ),
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
                provider_type_fn = getattr(registry, "provider_type", None)
                provider = (
                    str(provider_type_fn(target_instance) or "")
                    if callable(provider_type_fn)
                    else target_instance
                )
                provider_obj: Any = None
                get_provider = getattr(registry, "get", None)
                if callable(get_provider):
                    with suppress(Exception):
                        provider_obj = get_provider(target_instance)
                provider_cfg = getattr(cfg.llm, provider, None)
                model = str(
                    getattr(provider_obj, "_model", "") or getattr(provider_cfg, "model", "") or ""
                ).strip()

            async def _complete_probe() -> Any:
                async with ctx.llm_concurrency_gate.slot(caller="api.config_probe"):
                    messages = [
                        {"role": "system", "content": "Reply with only OK."},
                        {"role": "user", "content": "OpenBiliClaw connectivity probe."},
                    ]
                    if is_chain:
                        return await registry.complete(
                            messages,
                            temperature=0,
                            max_tokens=LLM_CONNECTIVITY_PROBE_MAX_TOKENS,
                            reasoning_effort="",
                        )
                    return await registry.complete_provider(
                        target_instance,
                        messages,
                        temperature=0,
                        max_tokens=LLM_CONNECTIVITY_PROBE_MAX_TOKENS,
                        reasoning_effort="",
                        model=model or None,
                    )

            response = await asyncio.wait_for(
                _complete_probe(),
                timeout=timeout_s,
            )
            ok = bool(str(getattr(response, "content", "") or "").strip())
            target_instance = str(getattr(response, "instance_id", "") or target_instance).strip()
            provider_type = getattr(registry, "provider_type", None)
            provider = str(
                getattr(response, "provider", "")
                or (provider_type(target_instance) if callable(provider_type) else "")
                or provider
            )
            response_model = str(getattr(response, "model", "") or model)
            label = (
                "LLM chain"
                if is_chain
                else "Fallback LLM instance"
                if is_fallback
                else "LLM instance"
            )
            return ConfigServiceProbeResponse(
                ok=ok,
                kind=kind,
                instance_id=target_instance,
                provider=provider,
                model=response_model,
                message=f"{label} is available." if ok else "",
                error="" if ok else f"{label} returned an empty response.",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except TimeoutError:
            return ConfigServiceProbeResponse(
                ok=False,
                kind=kind,
                instance_id=target_instance,
                provider=provider,
                model=model,
                error=f"LLM connectivity probe timed out after {timeout_s:g}s.",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return ConfigServiceProbeResponse(
                ok=False,
                kind=kind,
                instance_id=target_instance,
                provider=provider,
                model=model,
                error=str(exc),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

    async def _probe_embedding_config(cfg: Any) -> ConfigServiceProbeResponse:
        from openbiliclaw.llm.base import LLMRegistry
        from openbiliclaw.llm.registry import build_embedding_service

        started = time.perf_counter()
        emb_cfg = getattr(getattr(cfg, "llm", None), "embedding", None)
        provider = str(getattr(emb_cfg, "provider", "") or "").strip().lower()
        model = str(getattr(emb_cfg, "model", "") or "").strip()
        if not provider:
            return ConfigServiceProbeResponse(
                ok=False,
                kind="embedding",
                provider="",
                model=model,
                error="Embedding provider is not configured.",
            )
        try:
            service = build_embedding_service(cfg, LLMRegistry())
            if service is None:
                return ConfigServiceProbeResponse(
                    ok=False,
                    kind="embedding",
                    provider=provider,
                    model=model,
                    error="Embedding service could not be built from the submitted config.",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            probe = getattr(service, "probe", None)
            if not callable(probe):
                # Legacy/stub embedding service without a live probe —
                # building it successfully is the best signal we have.
                ok = True
            else:
                ok = bool(await asyncio.wait_for(probe(), timeout=15.0))
            return ConfigServiceProbeResponse(
                ok=ok,
                kind="embedding",
                provider=provider,
                model=model,
                message="Embedding provider is available." if ok else "",
                error="" if ok else "Embedding provider returned no vector.",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return ConfigServiceProbeResponse(
                ok=False,
                kind="embedding",
                provider=provider,
                model=model,
                error=str(exc),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

    async def _probe_network_proxy(mode: str, proxy: str) -> ConfigServiceProbeResponse:
        """Probe the submitted overseas routing policy without saving config.

        Fetches a tiny always-204 endpoint using the same explicit direct,
        system-inherited, or custom-proxy policy used by runtime clients.
        """
        import httpx

        from openbiliclaw.network import httpx_kwargs_for

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=5.0, **httpx_kwargs_for(mode, proxy)) as client:
                resp = await client.get("https://www.gstatic.com/generate_204")
            latency_ms = int((time.perf_counter() - started) * 1000)
            if resp.status_code in (200, 204):
                return ConfigServiceProbeResponse(
                    ok=True,
                    kind="network_proxy",
                    message="海外网络连通正常。",
                    latency_ms=latency_ms,
                )
            return ConfigServiceProbeResponse(
                ok=False,
                kind="network_proxy",
                error="unexpected_status",
                message=f"探测返回 HTTP {resp.status_code}。",
                latency_ms=latency_ms,
            )
        except httpx.ProxyError:
            return ConfigServiceProbeResponse(
                ok=False,
                kind="network_proxy",
                error="proxy_rejected",
                message="代理拒绝了连接，请检查代理认证 / 协议是否正确。",
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return ConfigServiceProbeResponse(
                ok=False,
                kind="network_proxy",
                error="proxy_unreachable",
                message="无法建立连接，请检查网络、系统代理或自定义代理是否可用。",
            )
        except (httpx.TimeoutException, TimeoutError):
            return ConfigServiceProbeResponse(
                ok=False,
                kind="network_proxy",
                error="timeout",
                message="海外网络访问超时（5 秒），请检查当前路由模式。",
            )
        except Exception as exc:  # noqa: BLE001 — surface a safe, classified failure
            return ConfigServiceProbeResponse(
                ok=False,
                kind="network_proxy",
                error="failed",
                message=f"海外网络探测失败:{type(exc).__name__}。",
            )

    @app.post("/api/config/probe-service", response_model=ConfigServiceProbeResponse)
    async def probe_config_service(payload: ConfigServiceProbeIn) -> ConfigServiceProbeResponse:
        """Probe submitted LLM / embedding / proxy settings without saving config.toml."""
        from copy import deepcopy

        from openbiliclaw.config import (
            load_config,
            normalize_outbound_proxy,
            normalize_outbound_proxy_mode,
        )

        update = payload.config if isinstance(payload.config, dict) else {}
        if payload.kind == "network_proxy":
            network_data = update.get("network")
            proxy_raw = ""
            mode_raw = ""
            if isinstance(network_data, dict):
                proxy_raw = str(network_data.get("proxy", ""))
                mode_raw = str(network_data.get("mode", ""))
            try:
                proxy = normalize_outbound_proxy(proxy_raw)
                # A payload without ``mode`` is the "never configured" case, so it
                # resolves the same way ``_build_network_config`` resolves an absent
                # key: legacy proxy-only clients stay ``custom``, everything else
                # takes the ``system`` default. Probing ``direct`` here would report
                # on a policy the runtime would not actually use.
                mode = (
                    normalize_outbound_proxy_mode(mode_raw)
                    if mode_raw.strip()
                    else ("custom" if proxy else "system")
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if mode == "custom" and not proxy:
                raise HTTPException(status_code=400, detail="自定义代理模式必须填写代理地址")
            return await _probe_network_proxy(mode, proxy)

        cfg = deepcopy(load_config())
        llm_data = update.get("llm")
        if isinstance(llm_data, dict):
            _apply_llm_update(cfg, llm_data)
        if payload.kind == "embedding":
            return await _probe_embedding_config(cfg)
        return await _probe_llm_config(
            cfg,
            kind=payload.kind,
            instance_id=payload.instance_id,
        )

    @app.post("/api/config/discover-models", response_model=ConfigModelDiscoveryResponse)
    async def discover_config_models(
        payload: ConfigModelDiscoveryIn,
    ) -> ConfigModelDiscoveryResponse:
        """List models for one submitted instance without saving config.toml."""
        from copy import deepcopy

        from openbiliclaw.config import load_config

        cfg = deepcopy(load_config())
        update = payload.config if isinstance(payload.config, dict) else {}
        llm_data = update.get("llm")
        if isinstance(llm_data, dict):
            _apply_llm_update(cfg, llm_data)
        return await _discover_llm_models(cfg, instance_id=payload.instance_id)

    @app.put("/api/config", response_model=ConfigUpdateResponse)
    async def update_config(
        request: Request, payload: ConfigUpdateIn
    ) -> ConfigUpdateResponse | JSONResponse:
        """Update configuration, persist to config.toml, and hot-reload runtime.

        Only the fields included in the request body are modified.
        After persisting, the backend attempts to rebuild all swappable
        runtime components so the new settings take effect immediately.
        """
        from openbiliclaw.config import (
            _DEFAULT_ADMISSION_MIN_SCORE,
            _DEFAULT_CANDIDATE_EVAL_CONCURRENCY,
            _DEFAULT_COGNITION_AWARENESS_EVENT_BATCH_SIZE,
            _DEFAULT_COGNITION_INSIGHT_NOTE_BATCH_SIZE,
            _DEFAULT_COGNITION_MAX_TOKENS,
            _DEFAULT_COPY_READY_TARGET_COUNT,
            _DEFAULT_DANMAKU_FETCH_LIMIT,
            _DEFAULT_DANMAKU_MAX_CHARS,
            _DEFAULT_DELIGHT_QUEUE_LIMIT,
            _DEFAULT_DISCOVERY_LIMIT,
            _DEFAULT_EVAL_MAX_WAIT_SECONDS,
            _DEFAULT_EVAL_MIN_BATCH_SIZE,
            _DEFAULT_EXPLORE_REFRESH_MINUTES,
            _DEFAULT_FEEDBACK_BATCH_THRESHOLD,
            _DEFAULT_KEYFRAME_FETCH_LIMIT,
            _DEFAULT_KEYFRAME_MAX_FRAMES,
            _DEFAULT_KEYWORD_DIGEST_GRACE_HOURS,
            _DEFAULT_LLM_BUDGET_MAX_CALLS,
            _DEFAULT_LLM_BUDGET_WINDOW_SECONDS,
            _DEFAULT_MULTIMODAL_BATCH_SIZE,
            _DEFAULT_MULTIMODAL_IMAGE_MAX_PX,
            _DEFAULT_MULTIMODAL_IMAGE_QUALITY,
            _DEFAULT_MULTIMODAL_IMAGE_TIMEOUT_SECONDS,
            _DEFAULT_PROACTIVE_PUSH_INTERVAL_SECONDS,
            _DEFAULT_REFRESH_CHECK_INTERVAL_SECONDS,
            _DEFAULT_SIGNAL_EVENT_THRESHOLD,
            _DEFAULT_SOURCE_INCREMENTAL_HOURS,
            _DEFAULT_SPECULATOR_IDLE_INTERVAL_MINUTES,
            _DEFAULT_TRENDING_REFRESH_MINUTES,
            _MAX_COGNITION_AWARENESS_EVENT_BATCH_SIZE,
            _MAX_COGNITION_INSIGHT_NOTE_BATCH_SIZE,
            _MAX_COGNITION_MAX_TOKENS,
            _MAX_COPY_READY_TARGET_COUNT,
            _MAX_EVAL_MAX_WAIT_SECONDS,
            _MAX_EVAL_MIN_BATCH_SIZE,
            _MIN_COGNITION_AWARENESS_EVENT_BATCH_SIZE,
            _MIN_COGNITION_INSIGHT_NOTE_BATCH_SIZE,
            _MIN_COGNITION_MAX_TOKENS,
            _MIN_COPY_READY_TARGET_COUNT,
            _MIN_EVAL_MAX_WAIT_SECONDS,
            _MIN_EVAL_MIN_BATCH_SIZE,
            _collect_config_issues,
            _default_config_path,
            _normalize_extension_disconnect_grace,
            _normalize_pool_source_shares,
            _normalize_probability,
            _normalize_scheduler_float,
            _normalize_scheduler_int,
            _normalize_source_incremental_hours,
            _validate_auto_update_check_interval,
            load_config,
            normalize_outbound_proxy,
            normalize_outbound_proxy_mode,
            normalize_tailnet_hostname,
            save_config,
            tailnet_override_source,
        )

        cfg = load_config()
        from pathlib import Path as _ConfigPath

        active_data_path = _active_runtime_data_path()
        update = payload.model_dump(exclude_none=True)
        # Preserve an explicit ``null`` for optional incremental overrides so
        # the API can restore inheritance; omitted fields still remain absent.
        if payload.scheduler is not None:
            update["scheduler"] = dict(payload.scheduler)
        reset_fields = [str(field) for field in update.pop("reset_fields", [])]
        suppress_background_llm_work = bool(update.pop("suppress_background_llm_work", False))
        unknown_reset_fields = [
            field for field in reset_fields if field not in _RESETTABLE_CONFIG_FIELDS
        ]
        if unknown_reset_fields:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unknown_reset_fields",
                    "fields": unknown_reset_fields,
                },
            )

        # Apply top-level scalars
        if "language" in update:
            cfg.language = str(update["language"])
        if "data_dir" in update:
            cfg.data_dir = str(update["data_dir"])

        tailnet_restart_required = False
        tailnet_bootstrap_credential = ""
        tailnet_advertise_tags: tuple[str, ...] = ()
        clear_tailnet_credential = False
        if "tailnet" in update:
            from openbiliclaw.runtime.tailnet_supervisor import (
                TailnetSupervisorError,
                normalize_tailnet_advertise_tags,
                normalize_tailnet_bootstrap_credential,
            )

            tailnet_data = update["tailnet"]
            previous_enabled = cfg.tailnet.enabled
            previous_hostname = cfg.tailnet.hostname
            requested_enabled = tailnet_data.get("enabled")
            requested_hostname = tailnet_data.get("hostname")

            if requested_enabled is not None:
                enabled_value = bool(requested_enabled)
                override = tailnet_override_source("enabled")
                if override and enabled_value != cfg.tailnet.enabled:
                    raise HTTPException(
                        status_code=409,
                        detail=f"{override} 正在覆盖 Tailnet 开关，请修改真实配置来源。",
                    )
                cfg.tailnet.enabled = enabled_value
            if requested_hostname is not None:
                try:
                    hostname_value = normalize_tailnet_hostname(requested_hostname)
                except Exception as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                override = tailnet_override_source("hostname")
                if override and hostname_value != cfg.tailnet.hostname:
                    raise HTTPException(
                        status_code=409,
                        detail=f"{override} 正在覆盖 Tailnet 节点名，请修改真实配置来源。",
                    )
                cfg.tailnet.hostname = hostname_value

            try:
                tailnet_bootstrap_credential = normalize_tailnet_bootstrap_credential(
                    tailnet_data.get("bootstrap_credential", "")
                )
                if tailnet_bootstrap_credential:
                    tailnet_advertise_tags = normalize_tailnet_advertise_tags(
                        tailnet_data.get("advertise_tags")
                    )
                    if (
                        tailnet_bootstrap_credential.startswith("tskey-client-")
                        and not tailnet_advertise_tags
                    ):
                        raise TailnetSupervisorError(
                            "Tailscale OAuth client secrets require at least one allowed device tag"
                        )
            except TailnetSupervisorError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            clear_tailnet_credential = bool(tailnet_data.get("clear_bootstrap_credential", False))
            if tailnet_bootstrap_credential and clear_tailnet_credential:
                raise HTTPException(
                    status_code=400,
                    detail="不能同时暂存并清除 Tailnet 启动凭据。",
                )
            if tailnet_bootstrap_credential and not cfg.tailnet.enabled:
                raise HTTPException(
                    status_code=400,
                    detail="暂存 Tailnet 启动凭据前请先开启应用内 Tailnet。",
                )
            if (tailnet_bootstrap_credential or clear_tailnet_credential) and not (
                _tailnet_bootstrap_request_allowed(request)
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Tailnet 启动凭据只能在运行后端的电脑上配置。",
                )
            tailnet_restart_required = (
                previous_enabled != cfg.tailnet.enabled
                or previous_hostname != cfg.tailnet.hostname
                or bool(tailnet_bootstrap_credential)
                or clear_tailnet_credential
            )

        # Apply LLM updates
        if "llm" in update:
            _apply_llm_update(cfg, update["llm"])

        # A masked GET echo looks like ``SESS****abcd`` — a long asterisk run
        # never appears in a genuine Cookie header, so use it (not a single
        # ``*``, which cookie values may legally contain) to detect echoes.
        def _is_masked_echo(value: str) -> bool:
            return "****" in value

        async def _gate_credential(slug: str, value: str) -> Any:
            """Run the shared write-time gate, or 400 carrying its verdict.

            This delegation *is* the fix for spec D4. This route writes four
            platforms' credentials — more platforms than any other endpoint in
            the codebase — yet validated none of them, while
            ``POST /api/bilibili/cookie`` refused the very same dead cookie
            after a live probe. Which page a user happened to paste into
            decided whether their credential was checked. It no longer does:
            both surfaces call :func:`validate_credential` with the same
            arguments, so they cannot reach different conclusions.

            Empty and masked-echo values never arrive here. Those are this
            route's *partial-update* semantics — "this field was not edited" —
            resolved by the callers before validation is reached, which is why
            they are not a strength difference between the two paths.

            Returns the verdict rather than discarding it. Equal validation
            strength was only half of invariant I5; the probe this just paid
            for is the same evidence the status endpoint and the verify button
            read, and throwing it away here is why one cookie showed
            ``verified`` when saved from the extension and ``unverified`` when
            saved from the settings page.
            """
            from openbiliclaw.api.source_auth.write import validate_credential

            verdict = await validate_credential(slug, "cookie", value, cfg=cfg)
            if verdict.ok:
                return verdict
            raise HTTPException(
                status_code=400,
                detail={"error": verdict.error_code, "message": verdict.message},
            )

        # Credential writes, deferred to the save path.
        #
        # ``config.toml`` is written once, at the end, under a lock and behind a
        # snapshot. The other three credential stores —
        # ``data/douyin_cookie.json``, ``data/x_cookie.json`` and rdt-cli's own
        # credential file — used to be written *where their fields are parsed*,
        # hundreds of lines before validation finishes. A single PUT carrying a
        # valid 抖音 cookie and an invalid ``[network]`` block therefore returned
        # 400 "配置校验失败，未写入" having already overwritten the cookie, and
        # those stores have neither snapshot nor rollback to undo it with.
        #
        # Deferring also puts them under ``_CONFIG_SAVE_LOCK``, so two
        # concurrent PUTs can no longer interleave into a state where the config
        # came from one request and the credentials from another. The
        # persistence stays in this handler by design (spec: it is mid
        # transaction on config.toml and must not have a shared writer flush its
        # pending edits); what changes is *when* it runs, not where it lives.
        pending_credential_writes: list[tuple[str, Callable[[], None]]] = []

        def _apply_source_date_preference_update(
            source_cfg: Any,
            source_data: dict[str, Any],
            source_name: str,
        ) -> None:
            """Validate and apply per-source recommendation date fields."""

            date_preference_fields = {
                "recommendation_date_preset",
                "recommendation_date_start",
                "recommendation_date_end",
                "recommendation_date_weight",
            }
            if not date_preference_fields & source_data.keys():
                return
            from openbiliclaw.recommendation.publication_preference import (
                PublicationDatePreference,
            )

            try:
                PublicationDatePreference(
                    preset=source_data.get(
                        "recommendation_date_preset",
                        getattr(source_cfg, "recommendation_date_preset", "all"),
                    ),
                    start_date=source_data.get(
                        "recommendation_date_start",
                        getattr(source_cfg, "recommendation_date_start", ""),
                    ),
                    end_date=source_data.get(
                        "recommendation_date_end",
                        getattr(source_cfg, "recommendation_date_end", ""),
                    ),
                    weight=source_data.get(
                        "recommendation_date_weight",
                        getattr(source_cfg, "recommendation_date_weight", 0.5),
                    ),
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": f"invalid_{source_name}_recommendation_date",
                        "message": str(exc),
                    },
                ) from exc
            for key in (
                "recommendation_date_preset",
                "recommendation_date_start",
                "recommendation_date_end",
            ):
                if key in source_data:
                    value = source_data[key]
                    setattr(source_cfg, key, "" if value is None else str(value))
            if "recommendation_date_weight" in source_data:
                source_cfg.recommendation_date_weight = source_data["recommendation_date_weight"]

        # Apply bilibili updates
        if "bilibili" in update:
            bdata = update["bilibili"]
            if "auth_method" in bdata:
                cfg.bilibili.auth_method = str(bdata["auth_method"])
            if "cookie" in bdata:
                # Mirror the api_key guards: never persist a masked echo, and
                # an empty field never wipes an existing cookie (the browser
                # extension's auto-sync owns refresh; a blank textarea on save
                # must not log the backend out).
                new_cookie = str(bdata["cookie"])
                if not _is_masked_echo(new_cookie) and (
                    new_cookie.strip() or not cfg.bilibili.cookie.strip()
                ):
                    if new_cookie.strip():
                        from openbiliclaw.api.source_auth.write import current_credential

                        bili_verdict = await _gate_credential("bilibili", new_cookie.strip())
                        # Read before the assignment below, or "did this change"
                        # compares the new value against itself.
                        bili_changed = new_cookie.strip() != current_credential("bilibili", cfg=cfg)

                        def _note_bilibili(
                            cookie: str = new_cookie.strip(),
                            verdict: Any = bili_verdict,
                            changed: bool = bili_changed,
                        ) -> None:
                            # B站's store on this route *is* config.toml, so
                            # ``save_config`` has already persisted it by the
                            # time this runs; only the verdict and the debounce
                            # are still outstanding.
                            _credential_landed(
                                "bilibili", verdict=verdict, value=cookie, changed=changed
                            )

                        pending_credential_writes.append(("bilibili", _note_bilibili))
                    cfg.bilibili.cookie = new_cookie
            if "browser_executable" in bdata:
                cfg.bilibili.browser_executable = str(bdata["browser_executable"])
            if "browser_headed" in bdata:
                cfg.bilibili.browser_headed = _as_bool(bdata["browser_headed"])

        # Apply source updates
        if "sources" in update:
            sources_data = update["sources"]
            if isinstance(sources_data, dict):
                browser_data = sources_data.get("browser")
                if isinstance(browser_data, dict):
                    if "cdp_url" in browser_data:
                        cfg.sources.browser_cdp_url = str(browser_data["cdp_url"])
                    if "headed" in browser_data:
                        cfg.sources.browser_headed = _as_bool(browser_data["headed"])

                bilibili_data = sources_data.get("bilibili")
                if isinstance(bilibili_data, dict):
                    if "enabled" in bilibili_data:
                        cfg.sources.bilibili.enabled = _as_bool(bilibili_data["enabled"])
                    if "min_interval_minutes" in bilibili_data:
                        cfg.sources.bilibili.min_interval_minutes = max(
                            0, int(bilibili_data["min_interval_minutes"])
                        )

                xhs_data = sources_data.get("xiaohongshu")
                if isinstance(xhs_data, dict):
                    if "enabled" in xhs_data:
                        cfg.sources.xiaohongshu.enabled = _as_bool(xhs_data["enabled"])
                    if "incremental_enabled" in xhs_data:
                        cfg.sources.xiaohongshu.incremental_enabled = _as_bool(
                            xhs_data["incremental_enabled"]
                        )
                    for key in (
                        "daily_search_budget",
                        "daily_creator_budget",
                        "task_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key in xhs_data:
                            setattr(cfg.sources.xiaohongshu, key, int(xhs_data[key]))

                dy_data = sources_data.get("douyin")
                if isinstance(dy_data, dict):
                    if "enabled" in dy_data:
                        cfg.sources.douyin.enabled = _as_bool(dy_data["enabled"])
                    if "incremental_enabled" in dy_data:
                        cfg.sources.douyin.incremental_enabled = _as_bool(
                            dy_data["incremental_enabled"]
                        )
                    if "mode" in dy_data:
                        cfg.sources.douyin.mode = str(dy_data["mode"])
                    if "cookie_env" in dy_data:
                        # The env var name has a sensible default; an emptied
                        # field keeps the current name rather than wiping it.
                        new_env = str(dy_data["cookie_env"]).strip()
                        if new_env:
                            cfg.sources.douyin.cookie_env = new_env
                    if "cookie" in dy_data:
                        # Manual paste from the settings pages. Routed to
                        # data/douyin_cookie.json (same store the extension
                        # auto-sync writes) — secrets never land in config.toml.
                        from openbiliclaw.sources.douyin_auth import (
                            DouyinCookieManager,
                            resolve_douyin_cookie,
                        )

                        new_cookie = str(dy_data["cookie"]).strip()
                        if new_cookie and not _is_masked_echo(new_cookie):
                            current = ""
                            with suppress(Exception):
                                current = resolve_douyin_cookie(
                                    data_dir=active_data_path,
                                    cookie_env=cfg.sources.douyin.cookie_env,
                                )
                            # Unchanged form echo → no write (env override
                            # must not be copied into the file needlessly), and
                            # no probe either: re-asking 抖音 about a cookie it
                            # already answered for is free risk.
                            if new_cookie != current:
                                dy_verdict = await _gate_credential("douyin", new_cookie)

                                def _store_douyin(
                                    cookie: str = new_cookie, verdict: Any = dy_verdict
                                ) -> None:
                                    DouyinCookieManager(active_data_path).set_cookie(
                                        cookie, source="config-update"
                                    )
                                    _credential_landed(
                                        "douyin", verdict=verdict, value=cookie, changed=True
                                    )

                                pending_credential_writes.append(("douyin", _store_douyin))
                    for key in (
                        "daily_search_budget",
                        "daily_hot_budget",
                        "daily_feed_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key in dy_data:
                            setattr(cfg.sources.douyin, key, int(dy_data[key]))

                yt_data = sources_data.get("youtube")
                if isinstance(yt_data, dict):
                    if "enabled" in yt_data:
                        cfg.sources.youtube.enabled = _as_bool(yt_data["enabled"])
                    if "incremental_enabled" in yt_data:
                        cfg.sources.youtube.incremental_enabled = _as_bool(
                            yt_data["incremental_enabled"]
                        )
                    for key in (
                        "daily_search_budget",
                        "daily_trending_budget",
                        "daily_channel_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key in yt_data:
                            setattr(cfg.sources.youtube, key, int(yt_data[key]))

                tw_data = sources_data.get("twitter")
                if isinstance(tw_data, dict):
                    if "enabled" in tw_data:
                        cfg.sources.twitter.enabled = _as_bool(tw_data["enabled"])
                    if "mode" in tw_data:
                        cfg.sources.twitter.mode = str(tw_data["mode"])
                    if "cookie_env" in tw_data:
                        new_env = str(tw_data["cookie_env"]).strip()
                        if new_env:
                            cfg.sources.twitter.cookie_env = new_env
                    if "cookie" in tw_data:
                        # Manual paste — routed to data/x_cookie.json like the
                        # extension auto-sync; never lands in config.toml.
                        new_cookie = str(tw_data["cookie"]).strip()
                        if new_cookie and not _is_masked_echo(new_cookie):
                            current = ""
                            with suppress(Exception):
                                current = resolve_x_cookie(
                                    data_dir=active_data_path,
                                    cookie_env=cfg.sources.twitter.cookie_env,
                                )
                            if new_cookie != current:
                                x_verdict = await _gate_credential("twitter", new_cookie)

                                def _store_twitter(
                                    cookie: str = new_cookie, verdict: Any = x_verdict
                                ) -> None:
                                    XCookieManager(active_data_path).set_cookie(
                                        cookie, source="config-update"
                                    )
                                    # A pasted valid cookie is a re-login signal,
                                    # same as the extension sync endpoint: lift
                                    # any missing/expired/blocked health block so
                                    # discovery retries instead of staying parked.
                                    # The gate above already established that
                                    # both required names are present — a jar
                                    # without them no longer reaches this point.
                                    if hasattr(ctx.database, "conn"):
                                        with suppress(Exception):
                                            from openbiliclaw.storage.x_health import (
                                                XSourceHealthStore,
                                            )

                                            XSourceHealthStore(ctx.database).clear_relogin_block()
                                    _credential_landed(
                                        "twitter", verdict=verdict, value=cookie, changed=True
                                    )

                                pending_credential_writes.append(("twitter", _store_twitter))
                    for key in (
                        "daily_search_budget",
                        "daily_feed_budget",
                        "daily_creator_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key in tw_data:
                            setattr(cfg.sources.twitter, key, int(tw_data[key]))

                zh_data = sources_data.get("zhihu")
                if isinstance(zh_data, dict):
                    if "enabled" in zh_data:
                        cfg.sources.zhihu.enabled = _as_bool(zh_data["enabled"])
                    if "incremental_enabled" in zh_data:
                        cfg.sources.zhihu.incremental_enabled = _as_bool(
                            zh_data["incremental_enabled"]
                        )
                    if "source_modes" in zh_data:
                        raw_modes = zh_data["source_modes"]
                        if isinstance(raw_modes, str):
                            modes = [part.strip() for part in raw_modes.split(",")]
                        elif isinstance(raw_modes, list):
                            modes = [str(part).strip() for part in raw_modes]
                        else:
                            modes = []
                        selected = [
                            mode
                            for mode in modes
                            if mode in {"search", "hot", "feed", "creator", "related"}
                        ]
                        if selected:
                            cfg.sources.zhihu.source_modes = tuple(dict.fromkeys(selected))
                    for key in (
                        "daily_search_budget",
                        "daily_hot_budget",
                        "daily_feed_budget",
                        "daily_creator_budget",
                        "daily_related_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key in zh_data:
                            setattr(cfg.sources.zhihu, key, int(zh_data[key]))

                reddit_data = sources_data.get("reddit")
                if isinstance(reddit_data, dict):
                    if "enabled" in reddit_data:
                        cfg.sources.reddit.enabled = _as_bool(reddit_data["enabled"])
                    if "incremental_enabled" in reddit_data:
                        cfg.sources.reddit.incremental_enabled = _as_bool(
                            reddit_data["incremental_enabled"]
                        )
                    if "backend" in reddit_data:
                        backend = str(reddit_data["backend"] or "").strip().lower()
                        if backend in {"openbiliclaw", "plugin"}:
                            backend = "extension"
                        cfg.sources.reddit.backend = (
                            backend if backend in {"extension", "opencli", "rdt", "auto"} else "rdt"
                        )
                    if "cookie" in reddit_data:
                        # Manual paste — routed to rdt-cli's credential store,
                        # same shape the extension auto-sync endpoint
                        # (POST /api/sources/reddit/cookie) writes; secrets
                        # never land in config.toml.
                        from openbiliclaw.sources.reddit_tasks import (
                            sync_rdt_credential_from_cookie_header,
                        )

                        new_cookie = str(reddit_data["cookie"]).strip()
                        if new_cookie and not _is_masked_echo(new_cookie):
                            # Reddit was already the one platform here that
                            # rejected visibly instead of pretending the paste
                            # took effect. The gate now decides that for all
                            # four, so the check happens *before* the write
                            # rather than being inferred from its result.
                            rd_verdict = await _gate_credential("reddit", new_cookie)

                            def _store_reddit(
                                cookie: str = new_cookie, verdict: Any = rd_verdict
                            ) -> None:
                                sync_result = sync_rdt_credential_from_cookie_header(
                                    cookie, source="config-update"
                                )
                                if not sync_result.has_cookie:
                                    # Unreachable through the gate, which has
                                    # already established a non-empty
                                    # ``reddit_session``. Kept because the two
                                    # sides parse the Cookie header with
                                    # different parsers: if they ever disagree,
                                    # the write must fail loudly with the real
                                    # reason rather than report a save that did
                                    # not happen (pitfall #7).
                                    raise RuntimeError(
                                        sync_result.message
                                        or "reddit credential store refused the cookie"
                                    )
                                _credential_landed(
                                    "reddit", verdict=verdict, value=cookie, changed=True
                                )

                            pending_credential_writes.append(("reddit", _store_reddit))
                    if "source_modes" in reddit_data:
                        raw_modes = reddit_data["source_modes"]
                        if isinstance(raw_modes, str):
                            modes = [part.strip() for part in raw_modes.split(",")]
                        elif isinstance(raw_modes, list):
                            modes = [str(part).strip() for part in raw_modes]
                        else:
                            modes = []
                        selected = [
                            mode
                            for mode in modes
                            if mode in {"search", "hot", "subreddit", "related"}
                        ]
                        if selected:
                            cfg.sources.reddit.source_modes = tuple(dict.fromkeys(selected))
                    for key in (
                        "daily_search_budget",
                        "daily_hot_budget",
                        "daily_subreddit_budget",
                        "daily_related_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key in reddit_data:
                            setattr(cfg.sources.reddit, key, int(reddit_data[key]))

                bangumi_data = sources_data.get("bangumi")
                if isinstance(bangumi_data, dict):
                    from openbiliclaw.sources.bangumi_client import (
                        validate_bangumi_access_token,
                        validate_bangumi_username,
                    )

                    if "enabled" in bangumi_data:
                        cfg.sources.bangumi.enabled = _as_bool(bangumi_data["enabled"])
                    if "username" in bangumi_data:
                        try:
                            cfg.sources.bangumi.username = validate_bangumi_username(
                                bangumi_data["username"]
                            )
                        except ValueError as exc:
                            raise HTTPException(status_code=400, detail=str(exc)) from exc
                    # A masked echo (the GET response never returns the real token)
                    # means "unchanged" — skip it so a settings round-trip cannot
                    # clobber a stored token with dots.
                    if "access_token" in bangumi_data and not _is_masked_echo(
                        str(bangumi_data["access_token"] or "").strip()
                    ):
                        try:
                            new_bangumi_token = validate_bangumi_access_token(
                                bangumi_data["access_token"]
                            )
                        except ValueError as exc:
                            raise HTTPException(status_code=400, detail=str(exc)) from exc
                        if new_bangumi_token:
                            # A new personal token identifies the account via
                            # /v0/me. Mirror the guided-init path: validate it
                            # live BEFORE persisting so a bad/expired token is
                            # rejected with its real cause (project rule 7), and
                            # record the /v0/me username as the source of truth.
                            from openbiliclaw.sources.bangumi_client import (
                                BangumiAPIError,
                                resolve_access_token_identity,
                            )

                            try:
                                resolved_username = await resolve_access_token_identity(
                                    new_bangumi_token
                                )
                            except BangumiAPIError as exc:
                                if exc.code == "unauthorized":
                                    return JSONResponse(
                                        {
                                            "error": "invalid_bangumi_access_token",
                                            "message": (
                                                "Bangumi 个人令牌被拒绝（缺失、错误或已过期）。"
                                                "请到 https://next.bgm.tv/demo/access-token "
                                                "重新生成后重试。"
                                            ),
                                        },
                                        status_code=400,
                                    )
                                return JSONResponse(
                                    {
                                        "error": "bangumi_token_check_failed",
                                        "message": (
                                            "校验 Bangumi 令牌时无法连接 Bangumi，请稍后重试。"
                                        ),
                                    },
                                    status_code=502,
                                )
                            cfg.sources.bangumi.access_token = new_bangumi_token
                            cfg.sources.bangumi.username = resolved_username
                        else:
                            # Explicit clear ("" submitted): drop the stored token
                            # with no network call.
                            cfg.sources.bangumi.access_token = ""
                        # Whether re-armed with a validated token or cleared, any
                        # stale rejection marker no longer applies.
                        if hasattr(ctx.database, "conn"):
                            from openbiliclaw.runtime.bangumi_producer import (
                                _clear_token_rejection,
                            )

                            with suppress(Exception):
                                _clear_token_rejection(ctx.database)
                    if "subject_types" in bangumi_data:
                        raw_types = bangumi_data["subject_types"]
                        if not isinstance(raw_types, list):
                            raise HTTPException(
                                status_code=400, detail="Bangumi subject_types 必须是数组"
                            )
                        selected_types = tuple(
                            dict.fromkeys(str(value).strip().lower() for value in raw_types)
                        )
                        if not selected_types or any(
                            value not in {"book", "anime", "music", "game", "real"}
                            for value in selected_types
                        ):
                            raise HTTPException(
                                status_code=400, detail="Bangumi subject_types 包含不支持的值"
                            )
                        cfg.sources.bangumi.subject_types = selected_types
                    if "source_modes" in bangumi_data:
                        raw_modes = bangumi_data["source_modes"]
                        if not isinstance(raw_modes, list):
                            raise HTTPException(
                                status_code=400, detail="Bangumi source_modes 必须是数组"
                            )
                        selected_modes = tuple(
                            dict.fromkeys(str(value).strip().lower() for value in raw_modes)
                        )
                        if not selected_modes or any(
                            value not in {"search", "ranked", "latest"} for value in selected_modes
                        ):
                            raise HTTPException(
                                status_code=400, detail="Bangumi source_modes 包含不支持的值"
                            )
                        cfg.sources.bangumi.source_modes = selected_modes
                    for key in (
                        "daily_search_budget",
                        "daily_ranked_budget",
                        "daily_latest_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                        "bootstrap_limit",
                    ):
                        if key not in bangumi_data:
                            continue
                        try:
                            value = int(bangumi_data[key])
                        except (TypeError, ValueError) as exc:
                            raise HTTPException(
                                status_code=400, detail=f"Bangumi {key} 必须是整数"
                            ) from exc
                        minimum = 1 if key == "bootstrap_limit" else 0
                        maximum = 1000 if key == "bootstrap_limit" else None
                        if value < minimum or (maximum is not None and value > maximum):
                            raise HTTPException(
                                status_code=400, detail=f"Bangumi {key} 超出允许范围"
                            )
                        setattr(cfg.sources.bangumi, key, value)

                github_data = sources_data.get("github")
                if isinstance(github_data, dict):
                    from openbiliclaw.config import (
                        GITHUB_ALLOWED_SOURCE_MODES,
                        GITHUB_CONFIG_INTEGER_LIMITS,
                        GITHUB_TOKEN_ENV,
                        normalize_github_source_config,
                    )
                    from openbiliclaw.sources.github_client import (
                        GitHubAPIError,
                        GitHubClient,
                        github_user_id,
                        github_user_login,
                        resolve_github_access_token,
                        validate_github_access_token,
                        validate_github_username,
                    )

                    allowed_github_fields = {
                        "enabled",
                        "username",
                        "access_token",
                        "access_token_set",  # read-only GET echo; ignored
                        "token_env",
                        "source_modes",
                        "recommendation_date_preset",
                        "recommendation_date_start",
                        "recommendation_date_end",
                        "recommendation_date_weight",
                        *GITHUB_CONFIG_INTEGER_LIMITS,
                    }
                    unknown_github_fields = sorted(set(github_data) - allowed_github_fields)
                    if unknown_github_fields:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "GitHub 包含不支持的配置字段: " + ", ".join(unknown_github_fields)
                            ),
                        )

                    github_cfg = cfg.sources.github
                    original_github_username = str(github_cfg.username or "").strip()
                    github_username_updated = False
                    if "enabled" in github_data:
                        github_cfg.enabled = _as_bool(github_data["enabled"])
                    if "username" in github_data:
                        try:
                            submitted_github_username = validate_github_username(
                                github_data["username"]
                            )
                        except ValueError as exc:
                            raise HTTPException(status_code=400, detail=str(exc)) from exc
                        github_username_updated = (
                            submitted_github_username.casefold()
                            != original_github_username.casefold()
                        )
                        github_cfg.username = submitted_github_username
                    if "token_env" in github_data:
                        submitted_token_env = str(github_data["token_env"] or "").strip()
                        if submitted_token_env != GITHUB_TOKEN_ENV:
                            raise HTTPException(
                                status_code=400,
                                detail=("GitHub token_env 只允许 OPENBILICLAW_GITHUB_TOKEN"),
                            )
                        github_cfg.token_env = GITHUB_TOKEN_ENV

                    github_token_updated = False
                    if "access_token" in github_data and not _is_masked_echo(
                        str(github_data["access_token"] or "").strip()
                    ):
                        github_token_updated = True
                        try:
                            new_github_token = validate_github_access_token(
                                github_data["access_token"]
                            )
                        except ValueError as exc:
                            raise HTTPException(status_code=400, detail=str(exc)) from exc

                        if new_github_token:
                            try:
                                async with GitHubClient(token=new_github_token) as github_client:
                                    identity = await github_client.get_user()
                                    resolved_username = github_user_login(identity)
                                    resolved_user_id = github_user_id(identity)
                                    # A renamed account may have a different login but
                                    # the same durable numeric id. Only a numeric-id
                                    # conflict is an identity mismatch.
                                    if (
                                        github_cfg.username
                                        and github_cfg.username.casefold()
                                        != resolved_username.casefold()
                                    ):
                                        claimed = await github_client.get_user_profile(
                                            github_cfg.username
                                        )
                                        if github_user_id(claimed) != resolved_user_id:
                                            return JSONResponse(
                                                {
                                                    "error": "github_identity_mismatch",
                                                    "message": (
                                                        "GitHub PAT 与填写的公开用户名属于"
                                                        "不同账号；为避免把他人 Starred "
                                                        "仓库写入画像，本次配置未保存。"
                                                    ),
                                                },
                                                status_code=400,
                                            )
                            except GitHubAPIError as exc:
                                if exc.code == "unauthorized":
                                    return JSONResponse(
                                        {
                                            "error": "invalid_github_access_token",
                                            "message": (
                                                "GitHub PAT 被拒绝（缺失、错误或已过期）。"
                                            ),
                                        },
                                        status_code=400,
                                    )
                                return JSONResponse(
                                    {
                                        "error": "github_token_check_failed",
                                        "message": (
                                            "校验 GitHub PAT 时无法完成只读 GET /user，"
                                            "请检查网络后重试。"
                                        ),
                                    },
                                    status_code=502,
                                )

                            github_cfg.access_token = new_github_token
                            github_cfg.username = resolved_username

                            def _note_github_token(
                                token: str = new_github_token,
                                username: str = resolved_username,
                                user_id: int = resolved_user_id,
                            ) -> None:
                                from openbiliclaw.api.source_auth.probe_cache import (
                                    LIVE_PROBES,
                                )
                                from openbiliclaw.api.source_auth.verify import (
                                    note_credential_changed,
                                )
                                from openbiliclaw.api.source_auth.write import (
                                    credential_fingerprint,
                                )
                                from openbiliclaw.runtime.github_producer import (
                                    clear_github_token_rejection,
                                )

                                clear_github_token_rejection(ctx.database)
                                LIVE_PROBES.record(
                                    "github",
                                    authenticated=True,
                                    detail=f"已识别 GitHub 账号（{username}）。",
                                    network_error=False,
                                    fingerprint=credential_fingerprint("github", token),
                                    username=username,
                                    user_id=user_id,
                                )
                                note_credential_changed("github")

                            pending_credential_writes.append(("github", _note_github_token))
                        else:
                            github_cfg.access_token = ""

                            def _note_github_token_cleared() -> None:
                                from openbiliclaw.api.source_auth.probe_cache import (
                                    LIVE_PROBES,
                                )
                                from openbiliclaw.api.source_auth.verify import (
                                    note_credential_changed,
                                )
                                from openbiliclaw.runtime.github_producer import (
                                    clear_github_token_rejection,
                                )

                                clear_github_token_rejection(ctx.database)
                                LIVE_PROBES.clear("github")
                                note_credential_changed("github")

                            pending_credential_writes.append(("github", _note_github_token_cleared))

                    if github_username_updated and not github_token_updated:
                        existing_github_token, _existing_origin = resolve_github_access_token(
                            github_cfg.access_token
                        )
                        if existing_github_token and github_cfg.username:
                            try:
                                async with GitHubClient(
                                    token=existing_github_token
                                ) as github_client:
                                    authenticated = await github_client.get_user()
                                    claimed = await github_client.get_user_profile(
                                        github_cfg.username
                                    )
                                if github_user_id(authenticated) != github_user_id(claimed):
                                    return JSONResponse(
                                        {
                                            "error": "github_identity_mismatch",
                                            "message": (
                                                "GitHub PAT 与填写的公开用户名属于不同账号；"
                                                "本次用户名未保存。"
                                            ),
                                        },
                                        status_code=400,
                                    )
                            except GitHubAPIError as exc:
                                if exc.code == "unauthorized":
                                    return JSONResponse(
                                        {
                                            "error": "invalid_github_access_token",
                                            "message": (
                                                "现有 GitHub PAT 已失效；请先更新或清除 PAT，"
                                                "再修改公开用户名。"
                                            ),
                                        },
                                        status_code=400,
                                    )
                                return JSONResponse(
                                    {
                                        "error": "github_token_check_failed",
                                        "message": (
                                            "校验 GitHub PAT 与公开用户名时无法完成官方只读请求，"
                                            "本次用户名未保存。"
                                        ),
                                    },
                                    status_code=502,
                                )

                    if "source_modes" in github_data:
                        raw_modes = github_data["source_modes"]
                        if not isinstance(raw_modes, list):
                            raise HTTPException(
                                status_code=400,
                                detail="GitHub source_modes 必须是数组",
                            )
                        selected_modes = tuple(
                            dict.fromkeys(str(value).strip().lower() for value in raw_modes)
                        )
                        if not selected_modes or any(
                            value not in GITHUB_ALLOWED_SOURCE_MODES for value in selected_modes
                        ):
                            raise HTTPException(
                                status_code=400,
                                detail="GitHub source_modes 包含不支持的值",
                            )
                        github_cfg.source_modes = selected_modes
                    for key, (minimum, maximum) in GITHUB_CONFIG_INTEGER_LIMITS.items():
                        if key not in github_data:
                            continue
                        try:
                            if isinstance(github_data[key], bool):
                                raise ValueError
                            value = int(github_data[key])
                        except (TypeError, ValueError) as exc:
                            raise HTTPException(
                                status_code=400,
                                detail=f"GitHub {key} 必须是整数",
                            ) from exc
                        if value < minimum or value > maximum:
                            raise HTTPException(
                                status_code=400,
                                detail=(f"GitHub {key} 必须在 {minimum}..{maximum} 之间"),
                            )
                        setattr(github_cfg, key, value)
                    try:
                        normalize_github_source_config(github_cfg, strict=True)
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail=str(exc)) from exc

                linuxdo_data = sources_data.get("linuxdo")
                if isinstance(linuxdo_data, dict):
                    if "enabled" in linuxdo_data:
                        cfg.sources.linuxdo.enabled = _as_bool(linuxdo_data["enabled"])
                    if "incremental_enabled" in linuxdo_data:
                        cfg.sources.linuxdo.incremental_enabled = _as_bool(
                            linuxdo_data["incremental_enabled"]
                        )
                    if "source_modes" in linuxdo_data:
                        raw_modes = linuxdo_data["source_modes"]
                        if not isinstance(raw_modes, list):
                            raise HTTPException(
                                status_code=400, detail="Linux.do source_modes 必须是数组"
                            )
                        selected_modes = tuple(
                            dict.fromkeys(str(value).strip().lower() for value in raw_modes)
                        )
                        if not selected_modes or any(
                            value not in {"search", "hot", "feed", "creator", "related"}
                            for value in selected_modes
                        ):
                            raise HTTPException(
                                status_code=400,
                                detail="Linux.do source_modes 包含不支持的值",
                            )
                        cfg.sources.linuxdo.source_modes = selected_modes
                    for key in (
                        "daily_search_budget",
                        "daily_hot_budget",
                        "daily_feed_budget",
                        "daily_creator_budget",
                        "daily_related_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                        "bootstrap_limit",
                    ):
                        if key not in linuxdo_data:
                            continue
                        try:
                            value = int(linuxdo_data[key])
                        except (TypeError, ValueError) as exc:
                            raise HTTPException(
                                status_code=400, detail=f"Linux.do {key} 必须是整数"
                            ) from exc
                        minimum = 1 if key == "bootstrap_limit" else 0
                        maximum = (
                            300
                            if key == "bootstrap_limit"
                            else 30
                            if key == "request_interval_seconds"
                            else None
                        )
                        if value < minimum or (maximum is not None and value > maximum):
                            raise HTTPException(
                                status_code=400, detail=f"Linux.do {key} 超出允许范围"
                            )
                        setattr(cfg.sources.linuxdo, key, value)
                v2ex_data = sources_data.get("v2ex")
                if isinstance(v2ex_data, dict):
                    from openbiliclaw.config import (
                        V2EX_CONFIG_INTEGER_LIMITS,
                        normalize_v2ex_list_field,
                        normalize_v2ex_source_config,
                    )
                    from openbiliclaw.sources.v2ex_client import (
                        V2EXAPIError,
                        V2EXClient,
                        member_username,
                        validate_v2ex_access_token,
                        validate_v2ex_username,
                    )

                    allowed_v2ex_fields = {
                        "enabled",
                        "incremental_enabled",
                        "username",
                        "access_token",
                        "token_env",
                        "source_modes",
                        "tab_modes",
                        "node_allowlist",
                        "node_blocklist",
                        "node_downweight",
                        "recommendation_date_preset",
                        "recommendation_date_start",
                        "recommendation_date_end",
                        "recommendation_date_weight",
                        *V2EX_CONFIG_INTEGER_LIMITS,
                    }
                    unknown_v2ex_fields = sorted(set(v2ex_data) - allowed_v2ex_fields)
                    if unknown_v2ex_fields:
                        raise HTTPException(
                            status_code=400,
                            detail=("V2EX 包含不支持的配置字段: " + ", ".join(unknown_v2ex_fields)),
                        )

                    v2ex_cfg = cfg.sources.v2ex
                    if "enabled" in v2ex_data:
                        v2ex_cfg.enabled = _as_bool(v2ex_data["enabled"])
                    if "incremental_enabled" in v2ex_data:
                        v2ex_cfg.incremental_enabled = _as_bool(v2ex_data["incremental_enabled"])
                    if "username" in v2ex_data:
                        try:
                            v2ex_cfg.username = validate_v2ex_username(v2ex_data["username"])
                        except ValueError as exc:
                            raise HTTPException(status_code=400, detail=str(exc)) from exc
                    if "token_env" in v2ex_data:
                        token_env = str(v2ex_data["token_env"] or "").strip()
                        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", token_env):
                            raise HTTPException(
                                status_code=400, detail="V2EX token_env 不是合法环境变量名"
                            )
                        v2ex_cfg.token_env = token_env
                    if "access_token" in v2ex_data and not _is_masked_echo(
                        str(v2ex_data["access_token"] or "").strip()
                    ):
                        try:
                            new_v2ex_token = validate_v2ex_access_token(v2ex_data["access_token"])
                        except ValueError as exc:
                            raise HTTPException(status_code=400, detail=str(exc)) from exc
                        if new_v2ex_token:
                            try:
                                async with V2EXClient(
                                    access_token=new_v2ex_token,
                                    request_interval_seconds=0,
                                ) as v2ex_client:
                                    member = await v2ex_client.get_member()
                                resolved_username = member_username(member)
                            except V2EXAPIError as exc:
                                if exc.code == "unauthorized":
                                    return JSONResponse(
                                        {
                                            "error": "invalid_v2ex_access_token",
                                            "message": "V2EX PAT 被拒绝（缺失、错误或已过期）。",
                                        },
                                        status_code=400,
                                    )
                                return JSONResponse(
                                    {
                                        "error": "v2ex_token_check_failed",
                                        "message": "校验 V2EX PAT 时无法连接 V2EX，请稍后重试。",
                                    },
                                    status_code=502,
                                )
                            v2ex_cfg.access_token = new_v2ex_token
                            v2ex_cfg.username = resolved_username
                        else:
                            v2ex_cfg.access_token = ""
                    list_fields = {
                        "source_modes",
                        "tab_modes",
                        "node_allowlist",
                        "node_blocklist",
                        "node_downweight",
                    }
                    for key in list_fields:
                        if key not in v2ex_data:
                            continue
                        raw_values = v2ex_data[key]
                        if not isinstance(raw_values, list):
                            raise HTTPException(status_code=400, detail=f"V2EX {key} 必须是数组")
                        try:
                            values = normalize_v2ex_list_field(key, raw_values, strict=True)
                        except ValueError as exc:
                            raise HTTPException(status_code=400, detail=str(exc)) from exc
                        setattr(v2ex_cfg, key, values)
                    for key, (minimum, maximum) in V2EX_CONFIG_INTEGER_LIMITS.items():
                        if key not in v2ex_data:
                            continue
                        try:
                            if isinstance(v2ex_data[key], bool):
                                raise ValueError
                            value = int(v2ex_data[key])
                        except (TypeError, ValueError) as exc:
                            raise HTTPException(
                                status_code=400, detail=f"V2EX {key} 必须是整数"
                            ) from exc
                        if value < minimum or value > maximum:
                            raise HTTPException(
                                status_code=400,
                                detail=f"V2EX {key} 必须在 {minimum}..{maximum} 之间",
                            )
                        setattr(v2ex_cfg, key, value)
                    try:
                        normalize_v2ex_source_config(v2ex_cfg, strict=True)
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail=str(exc)) from exc

                weibo_data = sources_data.get("weibo")
                if isinstance(weibo_data, dict):
                    if "enabled" in weibo_data:
                        cfg.sources.weibo.enabled = _as_bool(weibo_data["enabled"])
                    if "source_modes" in weibo_data:
                        raw_modes = weibo_data["source_modes"]
                        if not isinstance(raw_modes, list):
                            raise HTTPException(
                                status_code=400, detail="微博 source_modes 必须是数组"
                            )
                        selected_modes = tuple(
                            dict.fromkeys(
                                str(mode).strip() for mode in raw_modes if str(mode).strip()
                            )
                        )
                        if not selected_modes or any(
                            mode not in {"search", "hot", "creator"} for mode in selected_modes
                        ):
                            raise HTTPException(
                                status_code=400, detail="微博 source_modes 包含不支持的值"
                            )
                        if "creator" in selected_modes and not (
                            {"search", "hot"} & set(selected_modes)
                        ):
                            raise HTTPException(
                                status_code=400,
                                detail="微博 creator 模式需要同时启用 search 或 hot",
                            )
                        cfg.sources.weibo.source_modes = selected_modes
                    for key in (
                        "daily_search_budget",
                        "daily_hot_budget",
                        "daily_creator_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key not in weibo_data:
                            continue
                        raw_value = weibo_data[key]
                        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
                            raise HTTPException(status_code=400, detail=f"微博 {key} 必须是整数")
                        if raw_value < 0:
                            raise HTTPException(status_code=400, detail=f"微博 {key} 不能为负数")
                        setattr(cfg.sources.weibo, key, raw_value)

        # Apply per-source recommendation date preference updates. Validation
        # happens here, before save_config re-validates and writes.
        if "sources" in update and isinstance(update.get("sources"), dict):
            sources_data = update["sources"]
            source_config_by_slug = {
                "bilibili": cfg.sources.bilibili,
                "xiaohongshu": cfg.sources.xiaohongshu,
                "douyin": cfg.sources.douyin,
                "youtube": cfg.sources.youtube,
                "twitter": cfg.sources.twitter,
                "zhihu": cfg.sources.zhihu,
                "reddit": cfg.sources.reddit,
                "bangumi": cfg.sources.bangumi,
                "github": cfg.sources.github,
                "linuxdo": cfg.sources.linuxdo,
                "v2ex": cfg.sources.v2ex,
                "weibo": cfg.sources.weibo,
            }
            for slug, source_cfg in source_config_by_slug.items():
                source_data = sources_data.get(slug)
                if isinstance(source_data, dict):
                    _apply_source_date_preference_update(source_cfg, source_data, slug)

        # Apply scheduler updates
        if "scheduler" in update:
            sdata = update["scheduler"]
            scheduler_int_limits = {
                "refresh_check_interval_seconds": (
                    _DEFAULT_REFRESH_CHECK_INTERVAL_SECONDS,
                    15,
                    None,
                ),
                "eval_min_batch_size": (
                    _DEFAULT_EVAL_MIN_BATCH_SIZE,
                    _MIN_EVAL_MIN_BATCH_SIZE,
                    _MAX_EVAL_MIN_BATCH_SIZE,
                ),
                "copy_ready_target_count": (
                    _DEFAULT_COPY_READY_TARGET_COUNT,
                    _MIN_COPY_READY_TARGET_COUNT,
                    _MAX_COPY_READY_TARGET_COUNT,
                ),
                "signal_event_threshold": (_DEFAULT_SIGNAL_EVENT_THRESHOLD, 1, None),
                "trending_refresh_minutes": (_DEFAULT_TRENDING_REFRESH_MINUTES, 1, None),
                "explore_refresh_minutes": (_DEFAULT_EXPLORE_REFRESH_MINUTES, 1, None),
                "discovery_limit": (_DEFAULT_DISCOVERY_LIMIT, 1, 60),
                "delight_queue_limit": (_DEFAULT_DELIGHT_QUEUE_LIMIT, 1, 100),
                "proactive_push_interval_seconds": (
                    _DEFAULT_PROACTIVE_PUSH_INTERVAL_SECONDS,
                    30,
                    None,
                ),
                "llm_budget_max_calls": (_DEFAULT_LLM_BUDGET_MAX_CALLS, 0, None),
                "llm_budget_window_seconds": (_DEFAULT_LLM_BUDGET_WINDOW_SECONDS, 60, None),
                "speculator_idle_interval_minutes": (
                    _DEFAULT_SPECULATOR_IDLE_INTERVAL_MINUTES,
                    5,
                    None,
                ),
                "feedback_batch_threshold": (
                    _DEFAULT_FEEDBACK_BATCH_THRESHOLD,
                    1,
                    None,
                ),
                "avoidance_speculation_interval_minutes": (10, 1, None),
                "avoidance_speculation_ttl_days": (3, 1, None),
                "avoidance_speculation_cooldown_days": (7, 1, None),
                "avoidance_speculation_confirmation_threshold": (3, 1, None),
                "avoidance_speculation_max_active": (5, 1, None),
            }
            for key in (
                "enabled",
                "pause_on_extension_disconnect",
                "extension_disconnect_grace_seconds",
                "discovery_cron",
                "pool_target_count",
                "copy_ready_target_count",
                "account_sync_interval_hours",
                "source_incremental_enabled",
                "source_incremental_hours",
                "xhs_incremental_hours",
                "douyin_incremental_hours",
                "youtube_incremental_hours",
                "zhihu_incremental_hours",
                "reddit_incremental_hours",
                "linuxdo_incremental_hours",
                "v2ex_incremental_hours",
                "refresh_check_interval_seconds",
                "eval_min_batch_size",
                "eval_max_wait_seconds",
                "signal_event_threshold",
                "trending_refresh_minutes",
                "explore_refresh_minutes",
                "discovery_limit",
                "delight_queue_limit",
                "proactive_push_interval_seconds",
                "llm_budget_max_calls",
                "llm_budget_window_seconds",
                "speculator_idle_interval_minutes",
                "speculation_interval_minutes",
                "speculation_ttl_days",
                "speculation_cooldown_days",
                "speculation_confirmation_threshold",
                "speculation_max_active",
                "speculation_max_primary_interests",
                "speculation_max_secondary_interests",
                "avoidance_speculation_interval_minutes",
                "avoidance_speculation_ttl_days",
                "avoidance_speculation_cooldown_days",
                "avoidance_speculation_confirmation_threshold",
                "avoidance_speculation_max_active",
                "auto_update_enabled",
                "auto_update_check_interval_hours",
                "auto_update_allow_prerelease",
                "auto_update_allowed_remotes",
                "feedback_batch_threshold",
            ):
                if key in sdata:
                    current_val = getattr(cfg.scheduler, key)
                    if key == "auto_update_allowed_remotes":
                        next_remotes = _string_list(sdata[key])
                        if next_remotes:
                            setattr(cfg.scheduler, key, next_remotes)
                    elif key == "extension_disconnect_grace_seconds":
                        setattr(
                            cfg.scheduler,
                            key,
                            _normalize_extension_disconnect_grace(sdata[key]),
                        )
                    elif key == "auto_update_check_interval_hours":
                        try:
                            interval = _validate_auto_update_check_interval(sdata[key])
                        except ValueError as exc:
                            raise HTTPException(status_code=400, detail=str(exc)) from exc
                        setattr(cfg.scheduler, key, interval)
                    elif key in {
                        "xhs_incremental_hours",
                        "douyin_incremental_hours",
                        "youtube_incremental_hours",
                        "zhihu_incremental_hours",
                        "reddit_incremental_hours",
                        "linuxdo_incremental_hours",
                        "v2ex_incremental_hours",
                    }:
                        try:
                            # Douyin account bootstrap can foreground a task tab.
                            # Its null/reset value therefore returns to the safe
                            # default-off policy instead of inheriting the global
                            # source interval. An explicit 1..168 opts back in.
                            source_interval = (
                                0
                                if key == "douyin_incremental_hours" and sdata[key] is None
                                else _normalize_source_incremental_hours(
                                    sdata[key],
                                    default=None,
                                    allow_none=True,
                                    strict=True,
                                )
                            )
                        except ValueError as exc:
                            raise HTTPException(status_code=400, detail=str(exc)) from exc
                        setattr(cfg.scheduler, key, source_interval)
                    elif key == "source_incremental_hours":
                        try:
                            global_interval = _normalize_source_incremental_hours(
                                sdata[key],
                                default=_DEFAULT_SOURCE_INCREMENTAL_HOURS,
                                allow_none=False,
                                strict=True,
                            )
                        except ValueError as exc:
                            raise HTTPException(status_code=400, detail=str(exc)) from exc
                        if global_interval is None:
                            raise HTTPException(
                                status_code=400,
                                detail="source_incremental_hours must be an integer in 0..168",
                            )
                        setattr(cfg.scheduler, key, global_interval)
                    elif key == "eval_max_wait_seconds":
                        setattr(
                            cfg.scheduler,
                            key,
                            _normalize_scheduler_float(
                                sdata[key],
                                default=_DEFAULT_EVAL_MAX_WAIT_SECONDS,
                                min_value=_MIN_EVAL_MAX_WAIT_SECONDS,
                                max_value=_MAX_EVAL_MAX_WAIT_SECONDS,
                            ),
                        )
                    elif key in scheduler_int_limits:
                        default, min_value, max_value = scheduler_int_limits[key]
                        setattr(
                            cfg.scheduler,
                            key,
                            _normalize_scheduler_int(
                                sdata[key],
                                default=default,
                                min_value=min_value,
                                max_value=max_value,
                            ),
                        )
                    elif isinstance(current_val, bool):
                        setattr(cfg.scheduler, key, _as_bool(sdata[key]))
                    elif isinstance(current_val, int):
                        setattr(cfg.scheduler, key, int(sdata[key]))
                    else:
                        setattr(cfg.scheduler, key, str(sdata[key]))
            if "pool_source_shares" in sdata:
                cfg.scheduler.pool_source_shares = _normalize_pool_source_shares(
                    sdata["pool_source_shares"]
                )

        # Apply discovery planner / evaluator updates
        if "discovery" in update:
            ddata = update["discovery"]
            if isinstance(ddata, dict):
                discovery_int_limits = {
                    "keyword_digest_grace_hours": (
                        _DEFAULT_KEYWORD_DIGEST_GRACE_HOURS,
                        0,
                        168,
                    ),
                    "candidate_eval_concurrency": (
                        _DEFAULT_CANDIDATE_EVAL_CONCURRENCY,
                        1,
                        3,
                    ),
                    "multimodal_batch_size": (
                        _DEFAULT_MULTIMODAL_BATCH_SIZE,
                        1,
                        12,
                    ),
                    "multimodal_image_max_px": (
                        _DEFAULT_MULTIMODAL_IMAGE_MAX_PX,
                        128,
                        768,
                    ),
                    "multimodal_image_quality": (
                        _DEFAULT_MULTIMODAL_IMAGE_QUALITY,
                        40,
                        90,
                    ),
                    "multimodal_image_timeout_seconds": (
                        _DEFAULT_MULTIMODAL_IMAGE_TIMEOUT_SECONDS,
                        1,
                        20,
                    ),
                    "keyframe_max_frames": (_DEFAULT_KEYFRAME_MAX_FRAMES, 1, 12),
                    "keyframe_fetch_limit": (_DEFAULT_KEYFRAME_FETCH_LIMIT, 1, 200),
                    "danmaku_fetch_limit": (_DEFAULT_DANMAKU_FETCH_LIMIT, 1, 200),
                    "danmaku_max_chars": (_DEFAULT_DANMAKU_MAX_CHARS, 100, 2000),
                }
                if "multimodal_evaluation_enabled" in ddata:
                    cfg.discovery.multimodal_evaluation_enabled = _as_bool(
                        ddata["multimodal_evaluation_enabled"]
                    )
                if "visual_profile_enabled" in ddata:
                    cfg.discovery.visual_profile_enabled = _as_bool(ddata["visual_profile_enabled"])
                if "keyframe_enabled" in ddata:
                    cfg.discovery.keyframe_enabled = _as_bool(ddata["keyframe_enabled"])
                if "danmaku_enabled" in ddata:
                    cfg.discovery.danmaku_enabled = _as_bool(ddata["danmaku_enabled"])
                if "admission_min_score" in ddata:
                    cfg.discovery.admission_min_score = _normalize_probability(
                        ddata["admission_min_score"],
                        default=_DEFAULT_ADMISSION_MIN_SCORE,
                    )
                if "eval_prefilter_mode" in ddata:
                    eval_prefilter_mode = str(ddata["eval_prefilter_mode"] or "").strip().lower()
                    if eval_prefilter_mode not in {"off", "shadow", "enforce"}:
                        raise HTTPException(
                            status_code=422,
                            detail="discovery.eval_prefilter_mode must be off, shadow, or enforce",
                        )
                    cfg.discovery.eval_prefilter_mode = eval_prefilter_mode
                for key, (default, min_value, max_value) in discovery_int_limits.items():
                    if key in ddata:
                        setattr(
                            cfg.discovery,
                            key,
                            _normalize_scheduler_int(
                                ddata[key],
                                default=default,
                                min_value=min_value,
                                max_value=max_value,
                            ),
                        )
                # Keyword-generation mode: a UI/API-derived enum translated to the
                # two canonical DiscoveryConfig booleans. discovery is a raw dict
                # (Pydantic does NOT validate the nested Literal), so validate the
                # value manually → 422 on anything illegal. This block runs LAST
                # in the discovery section so that if a request also carries the
                # raw inspiration_* booleans, the mode wins (deterministic UI
                # semantics). ``keyword_generation_mode`` itself is never set on
                # cfg.discovery / written to config.toml — only the two booleans.
                if "keyword_generation_mode" in ddata:
                    raw_mode = ddata["keyword_generation_mode"]
                    if raw_mode not in ("legacy", "hybrid", "inspiration"):
                        raise HTTPException(
                            status_code=422,
                            detail=(
                                "discovery.keyword_generation_mode must be one of: "
                                "legacy, hybrid, inspiration"
                            ),
                        )
                    enabled, replace = _mode_to_flags(cast("str", raw_mode))
                    cfg.discovery.inspiration_search_enabled = enabled
                    cfg.discovery.inspiration_replace_merged_keywords = replace

        # Apply saved-sync updates
        if "saved_sync" in update:
            saved_sync_data = update["saved_sync"]
            if "auto_sync_enabled" in saved_sync_data:
                cfg.saved_sync.auto_sync_enabled = saved_sync_data["auto_sync_enabled"]

        # Apply storage updates
        if "storage" in update:
            stdata = update["storage"]
            if "db_path" in stdata:
                cfg.storage.db_path = str(stdata["db_path"])

        # Apply logging updates
        if "logging" in update:
            ldata = update["logging"]
            for key in ("level", "file_level", "directory", "filename"):
                if key in ldata:
                    setattr(cfg.logging, key, str(ldata[key]))
            for key in (
                "max_file_size_mb",
                "backup_count",
                "aggregate_budget_mb",
                "unmanaged_truncate_mb",
                "unmanaged_max_age_days",
            ):
                if key in ldata:
                    setattr(cfg.logging, key, int(ldata[key]))

        # Apply the overseas routing policy. Proxy-only payloads from older UI
        # versions carry no opinion about ``mode``, so they resolve exactly as an
        # absent ``[network].mode`` key does in ``_build_network_config``: a
        # non-empty proxy is still custom, and clearing the proxy falls back to
        # the ``system`` default rather than pinning the user to direct.
        if "network" in update:
            ndata = update["network"]
            if isinstance(ndata, dict):
                mode_supplied = "mode" in ndata
                if mode_supplied:
                    try:
                        cfg.network.mode = normalize_outbound_proxy_mode(str(ndata["mode"]))
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail=str(exc)) from exc
                if "proxy" in ndata:
                    raw_proxy = str(ndata["proxy"])
                    # A masked GET echo must never overwrite stored credentials.
                    if not _is_masked_proxy_echo(raw_proxy):
                        try:
                            cfg.network.proxy = normalize_outbound_proxy(raw_proxy)
                        except ValueError as exc:
                            raise HTTPException(status_code=400, detail=str(exc)) from exc
                        if not mode_supplied:
                            cfg.network.mode = "custom" if cfg.network.proxy else "system"
                if cfg.network.mode == "custom" and not cfg.network.proxy:
                    raise HTTPException(status_code=400, detail="自定义代理模式必须填写代理地址")

        # Apply soul posture-gate updates (Phase 3). Mode validity is checked by
        # _collect_config_issues; enforce readiness is a DB-backed save-time
        # guard applied just below.
        if "soul" in update and isinstance(update["soul"], dict):
            sdata = update["soul"]
            for prompt_view_field in (
                "preference_prompt_view",
                "awareness_prompt_view",
                "insight_prompt_view",
            ):
                if prompt_view_field in sdata:
                    setattr(
                        cfg.soul,
                        prompt_view_field,
                        str(sdata[prompt_view_field]).strip().lower(),
                    )
            if "posture_gate_mode" in sdata:
                cfg.soul.posture_gate_mode = str(sdata["posture_gate_mode"]).strip().lower()
            if "posture_gate_force_enforce" in sdata:
                cfg.soul.posture_gate_force_enforce = bool(sdata["posture_gate_force_enforce"])
            soul_int_limits = {
                "awareness_event_batch_size": (
                    _DEFAULT_COGNITION_AWARENESS_EVENT_BATCH_SIZE,
                    _MIN_COGNITION_AWARENESS_EVENT_BATCH_SIZE,
                    _MAX_COGNITION_AWARENESS_EVENT_BATCH_SIZE,
                ),
                "insight_note_batch_size": (
                    _DEFAULT_COGNITION_INSIGHT_NOTE_BATCH_SIZE,
                    _MIN_COGNITION_INSIGHT_NOTE_BATCH_SIZE,
                    _MAX_COGNITION_INSIGHT_NOTE_BATCH_SIZE,
                ),
                "cognition_max_tokens": (
                    _DEFAULT_COGNITION_MAX_TOKENS,
                    _MIN_COGNITION_MAX_TOKENS,
                    _MAX_COGNITION_MAX_TOKENS,
                ),
            }
            for soul_int_field, (default, min_value, max_value) in soul_int_limits.items():
                if soul_int_field in sdata:
                    setattr(
                        cfg.soul,
                        soul_int_field,
                        _normalize_scheduler_int(
                            sdata[soul_int_field],
                            default=default,
                            min_value=min_value,
                            max_value=max_value,
                        ),
                    )

        for field in reset_fields:
            target = _RESETTABLE_CONFIG_FIELDS[field]
            section = getattr(cfg, target[0])
            subsection = getattr(section, target[1])
            setattr(subsection, target[2], "")

        issues = _validate_llm_buildable(cfg, _collect_config_issues(cfg))
        posture_issue = _posture_gate_enforce_issue(cfg, getattr(ctx, "database", None))
        if posture_issue is not None:
            issues.append(posture_issue)
        if any(getattr(issue, "severity", "warning") == "blocking" for issue in issues):
            response = ConfigUpdateResponse(
                ok=False,
                config=_config_to_response(
                    cfg,
                    issues,
                    mask_keys=True,
                    degraded=bool(getattr(ctx, "degraded", False)),
                    degraded_reason=str(getattr(ctx, "degraded_reason", "")),
                ),
                message="配置校验失败，未写入 config.toml。",
                reloaded=False,
                rollback_applied=False,
                restart_required=False,
            )
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json"),
            )

        desired_data_path = _ConfigPath(cfg.data_path).expanduser().resolve()
        data_dir_restart_required = desired_data_path != active_data_path
        restart_required = data_dir_restart_required or tailnet_restart_required
        async with _CONFIG_SAVE_LOCK:
            # gui-init D1 / spec §5b: re-check inside the lock. The middleware
            # gated this path on init_active before the handler ran, but a run
            # could have been reserved in between; saving + rebuilding config
            # mid-init would swap components the run is using.
            if _init_active_now():
                return JSONResponse(
                    {"error": "init_running", "detail": "初始化进行中，请稍后再保存配置"},
                    status_code=409,
                )
            config_path = _default_config_path()
            try:
                _snapshot_config_file(config_path)
            except Exception as exc:
                logger.exception("Config snapshot failed — refusing to overwrite config.toml")
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": "config_snapshot_failed",
                        "message": f"couldn't snapshot config, refusing to risk overwrite: {exc}",
                    },
                )

            saved_path = save_config(cfg)
            logger.info("Configuration saved to %s", saved_path)

            # Only now do the external credential stores get touched. Every
            # validation has passed, the 400 and 409 exits are behind us, and
            # config.toml is committed — so "the response said saved" and "the
            # credential is on disk" can no longer disagree. Ordered after
            # ``save_config`` deliberately: a failure to write the config aborts
            # before any credential moves, which is the direction that leaves
            # the least behind.
            #
            # No transaction spans four independent stores, so an I/O failure
            # part-way through genuinely leaves some applied. That cannot be
            # prevented here; what it must not do is surface as an opaque 500.
            # The raised error names the platform that failed and the ones that
            # already landed, because "which of my credentials actually saved?"
            # is the only question worth answering at that point (pitfall #7).
            landed: list[str] = []
            for slug, apply_credential in pending_credential_writes:
                try:
                    apply_credential()
                except Exception as exc:
                    logger.exception("credential write failed after config save: slug=%s", slug)
                    already = ", ".join(landed) if landed else "none"
                    raise RuntimeError(
                        f"config.toml saved, but storing the {slug} credential failed: {exc}. "
                        f"Credentials already written this request: {already}. "
                        f"Re-save to retry; the write is idempotent."
                    ) from exc
                landed.append(slug)

            if "tailnet" in update:
                from openbiliclaw.runtime.tailnet_supervisor import (
                    clear_tailnet_bootstrap,
                    stage_tailnet_bootstrap,
                    tailnet_bootstrap_path,
                )

                try:
                    if tailnet_bootstrap_credential:
                        stage_tailnet_bootstrap(
                            cfg,
                            tailnet_bootstrap_credential,
                            tailnet_advertise_tags,
                        )
                    elif clear_tailnet_credential or not cfg.tailnet.enabled:
                        clear_tailnet_bootstrap(cfg)
                        active_tailnet_config = _pin_active_runtime_config(cfg)
                        if tailnet_bootstrap_path(active_tailnet_config) != tailnet_bootstrap_path(
                            cfg
                        ):
                            clear_tailnet_bootstrap(active_tailnet_config)
                except Exception as exc:
                    logger.exception("Tailnet bootstrap credential write failed")
                    raise RuntimeError(
                        "config.toml 已保存，但 Tailnet 单次启动凭据处理失败；"
                        "请在本机设置页重新保存。"
                    ) from exc

            nonlocal config_apply_revision
            config_apply_revision += 1
            # The process-lifetime migration guard protects active_data_path.
            # Persist a new data_dir for the next startup, but never publish it
            # into this process without first acquiring that directory's lock.
            runtime_config = _pin_active_runtime_config(cfg)
            item = _QueuedConfigApply(
                revision=config_apply_revision,
                config=runtime_config,
                saved_path=saved_path,
                run_post_reload_llm_work=not suppress_background_llm_work,
                restart_required=restart_required,
            )

            # 持久化与运行时应用是两个阶段。统一进入已有 latest-wins 队列，
            # 避免一次后台 LLM 或事件排空把交互式保存请求挂住数十秒。
            _enqueue_config_apply(item)
            queued_response = ConfigUpdateResponse(
                ok=True,
                config=_config_to_response(cfg, issues, mask_keys=True),
                message=(
                    f"配置已保存到 {saved_path}；"
                    + (
                        "data_dir 与应用内 Tailnet 将在完全重启后生效，"
                        if data_dir_restart_required and tailnet_restart_required
                        else "data_dir 将在完全重启后生效，"
                        if data_dir_restart_required
                        else "应用内 Tailnet 将在完全重启后生效，"
                    )
                    + "其余配置正在后台应用。"
                    if restart_required
                    else f"配置已保存到 {saved_path}，正在后台应用。"
                ),
                reloaded=False,
                rollback_applied=False,
                restart_required=restart_required,
                apply_state="queued",
                apply_revision=item.revision,
            )
            return JSONResponse(
                status_code=202,
                content=queued_response.model_dump(mode="json"),
            )

    def _normalize_enabled_sources_override(
        raw_enabled: dict[str, bool] | None,
        fallback: dict[str, bool],
    ) -> dict[str, bool]:
        if raw_enabled is None:
            return fallback
        enabled: dict[str, bool] = {}
        for source in _SOURCE_SHARE_ORDER:
            enabled[source] = bool(raw_enabled.get(source, fallback.get(source, False)))
        return {source: enabled.get(source, False) for source in _SOURCE_SHARE_ORDER}

    def _build_source_share_suggestion_response(
        payload: SourceShareSuggestionIn | None = None,
    ) -> SourceShareSuggestionResponse:
        """Suggest pool source shares from observed platform event counts."""
        from openbiliclaw.config import load_config
        from openbiliclaw.runtime.source_policy import (
            source_enabled_map,
            suggest_pool_source_shares,
        )

        cfg = load_config()
        event_counts = _count_events_by_source_platform(ctx.database)
        enabled_sources = _normalize_enabled_sources_override(
            payload.enabled_sources if payload else None,
            source_enabled_map(cfg),
        )
        suggested_shares = suggest_pool_source_shares(
            event_counts,
            enabled_sources=enabled_sources,
            configured_shares=(
                payload.configured_shares
                if payload and payload.configured_shares is not None
                else cfg.scheduler.pool_source_shares
            ),
        )
        return SourceShareSuggestionResponse(
            event_counts=event_counts,
            enabled_sources=enabled_sources,
            suggested_shares=suggested_shares,
        )

    @app.get(
        "/api/config/source-share-suggestion",
        response_model=SourceShareSuggestionResponse,
    )
    def source_share_suggestion() -> SourceShareSuggestionResponse:
        """Suggest pool source shares from saved config switches."""
        return _build_source_share_suggestion_response()

    @app.post(
        "/api/config/source-share-suggestion",
        response_model=SourceShareSuggestionResponse,
    )
    def source_share_suggestion_for_form(
        payload: SourceShareSuggestionIn,
    ) -> SourceShareSuggestionResponse:
        """Suggest pool source shares from unsaved settings form state."""
        return _build_source_share_suggestion_response(payload)

    # v0.3.57+: one-shot purge of self-authored xhs pool rows that
    # accumulated before the per-path filter was wired in. No-op on
    # fresh installs (no persisted self_info → nothing to scan against);
    # repairs the pool the first time the user upgrades after having
    # browsed XHS while logged in.
    _existing_self_info = _load_xhs_self_info()
    if _existing_self_info:
        _purged = _purge_self_authored_pool_items(ctx.database, _existing_self_info)
        if _purged:
            logger.info(
                "startup purge: suppressed %d self-authored xhs pool item(s) (nickname=%r)",
                _purged,
                _existing_self_info.get("nickname", ""),
            )

    # ── Mobile Web UI ───────────────────────────────────────────
    from pathlib import Path as _Path

    from fastapi.staticfiles import StaticFiles as _StaticFiles

    _web_dir = _Path(__file__).resolve().parent.parent / "web"
    _shared_web_dir = _web_dir / "shared"
    if _web_dir.is_dir():
        _favicon_path = _web_dir / "icon-32.png"

        @app.get("/favicon.ico", include_in_schema=False)
        def _favicon() -> FileResponse:
            if not _favicon_path.is_file():
                raise HTTPException(status_code=404, detail="favicon not found")
            return FileResponse(_favicon_path, media_type="image/png")

        app.mount("/m", _StaticFiles(directory=_web_dir, html=True), name="mobile-web")

    # ── Shared frontend modules ──────────────────────────────────
    # Its own mount rather than a subdirectory of an existing one: `/web` is
    # rooted at `web/desktop`, so a file in `web/shared/` is only reachable
    # through the *mobile* mount (`/m/shared/…`). Serving cross-surface code
    # from a surface-specific URL is how it ends up copied instead of shared.
    if _shared_web_dir.is_dir():
        app.mount("/shared", _StaticFiles(directory=_shared_web_dir), name="shared-web")

    # ── Desktop Web UI ───────────────────────────────────────────
    _desktop_dir = _Path(__file__).resolve().parent.parent / "web" / "desktop"
    if _desktop_dir.is_dir():
        _desktop_index_path = _desktop_dir / "index.html"

        def _desktop_asset_version() -> str:
            import hashlib

            digest = hashlib.sha256()
            # Paths are relative to the desktop root except the shared modules,
            # which live outside it — an upgrade that only changed
            # shared renderers would otherwise be served from cache forever.
            for relative, root in (
                ("assets/css/app.css", _desktop_dir),
                ("assets/css/classic.css", _desktop_dir),
                ("assets/js/app.js", _desktop_dir),
                ("dialogue-confirmation.js", _shared_web_dir),
                ("source-status.js", _shared_web_dir),
            ):
                path = root / relative
                if not path.is_file():
                    continue
                stat = path.stat()
                digest.update(relative.encode("utf-8"))
                digest.update(str(stat.st_mtime_ns).encode("ascii"))
                digest.update(str(stat.st_size).encode("ascii"))
            return digest.hexdigest()[:12]

        def _desktop_index_response() -> Response:
            if not _desktop_index_path.is_file():
                raise HTTPException(status_code=404, detail="desktop web index not found")
            version = _desktop_asset_version()
            html = _desktop_index_path.read_text(encoding="utf-8")
            html = html.replace(
                'href="/web/assets/css/app.css"',
                f'href="/web/assets/css/app.css?v={version}"',
            )
            html = html.replace(
                'href="/web/assets/css/classic.css"',
                f'href="/web/assets/css/classic.css?v={version}"',
            )
            html = html.replace(
                'src="/web/assets/js/app.js"',
                f'src="/web/assets/js/app.js?v={version}"',
            )
            html = html.replace(
                'src="/shared/dialogue-confirmation.js"',
                f'src="/shared/dialogue-confirmation.js?v={version}"',
            )
            html = html.replace(
                'src="/shared/source-status.js"',
                f'src="/shared/source-status.js?v={version}"',
            )
            return Response(
                html,
                media_type="text/html; charset=utf-8",
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/web", include_in_schema=False)
        def _desktop_index_no_slash() -> Response:
            return _desktop_index_response()

        @app.get("/web/", include_in_schema=False)
        def _desktop_index_slash() -> Response:
            return _desktop_index_response()

        app.mount("/web", _StaticFiles(directory=_desktop_dir, html=True), name="desktop-web")

        @app.get("/", include_in_schema=False)
        def _root_redirect() -> RedirectResponse:
            # Mirror packaging/entry.py's _decide_landing_path for browsers
            # that reach the port without the packaged launcher (git/docker
            # installs, manual visits): a degraded backend or one whose init
            # never completed lands on the setup wizard, not the SPA. Unknown
            # readiness keeps the /web fallback — the SPA's onboarding gate
            # is the safety net, not a wall.
            needs_setup = bool(getattr(ctx, "degraded", False)) or _health_profile_ready() is False
            if needs_setup and (_web_dir / "setup").is_dir():
                return RedirectResponse(url="/setup/", status_code=302)
            return RedirectResponse(url="/web", status_code=302)

    # ── First-run Setup Wizard ──────────────────────────────────
    # Self-contained onboarding page opened on first launch by the packaged
    # app (packaging/entry.py). Guides provider/key + B站 + done, then sends
    # the user to /web. Kept isolated from the main desktop SPA on purpose.
    _setup_dir = _Path(__file__).resolve().parent.parent / "web" / "setup"
    if _setup_dir.is_dir():
        app.mount("/setup", _StaticFiles(directory=_setup_dir, html=True), name="setup-wizard")

    return app
