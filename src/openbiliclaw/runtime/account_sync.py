"""Periodic account-side sync for long-term Bilibili signals."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, cast

import httpx

from openbiliclaw.bilibili.api import (
    BilibiliAPIError,
    BilibiliAuthExpiredError,
    favorite_item_is_dead,
)
from openbiliclaw.llm.base import (
    classify_llm_failure_kind,
    classify_llm_unavailability,
    safe_llm_failure_message,
)

# Cross-source identity-key helpers live in the shared ``sources.identity_keys``
# module (promoted in event-capture-completion Phase 0 so retraction discounting
# can key on the same normalization). Aliased to the historic private names to
# keep this module's call sites unchanged.
from openbiliclaw.sources.identity_keys import dedup_key as _dedup_key
from openbiliclaw.sources.identity_keys import tweet_id_from_url as _tweet_id_from_url
from openbiliclaw.sources.x_client import (
    XAuthError,
    XBlockedError,
    XClientError,
    XMissingCookieError,
    XRateLimitError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_ANALYSIS_TIMEOUT_SECONDS = 360.0
_PROFILE_ANALYSIS_TIMEOUT_MESSAGE = (
    "AI 偏好分析等待模型服务超过 6 分钟仍未返回结果，已自动停止，避免继续卡住。"
    "常见原因是 Base URL、模型名或代理配置错误，网络无法访问模型服务，"
    "或模型服务响应过慢。请到模型设置测试 AI 服务，修正后重试。"
)

# Cross-source dedup lookback. 48h > 2× the 6h sync interval (one full missed
# cycle) while comfortably shorter than typical organic re-watch gaps, so a
# genuine re-watch after the window still produces a fresh signal.
_CROSS_SOURCE_DEDUP_WINDOW_HOURS = 48

# X (Twitter) scheduled incremental sync bounds — mirror init's read limits.
_X_FETCH_LIMIT = 200
# Bound the per-account seen-tweet-id state so it can't grow without limit.
_X_ID_CAP = 2000
# First-sync seeding reads *all* persisted X events (init may be months old), so
# the lookback is effectively unwindowed rather than the 48h dedup window.
_X_SEED_WINDOW_HOURS = 24 * 365 * 10

# Stable stage codes persisted in ``account_sync_state.json`` and exposed by
# ``/api/runtime-status``. They deliberately contain no provider detail or
# credentials: support can identify the failed subsystem without displaying a
# raw exception to the user.
_SYNC_STAGE_LABELS = {
    "bilibili_history": "观看历史",
    "bilibili_favorites": "收藏夹",
    "bilibili_following": "关注列表",
    "x_preferences": "点赞与书签",
    "x_likes": "点赞",
    "x_bookmarks": "书签",
    "profile_analysis": "画像分析",
}
_MAX_SYNC_ISSUES = 8
_SYNC_ISSUE_KINDS = frozenset(
    {
        "api_error",
        "auth_expired",
        "auth_failed",
        "connection",
        "invalid_response",
        "model_not_found",
        "moderation",
        "network",
        "no_provider",
        "quota_exhausted",
        "rate_limited",
        "server_error",
        "ssl",
        "timeout",
        "unexpected_error",
        "x_auth_expired",
        "x_blocked",
        "x_rate_limited",
    }
)

# Issue kinds that describe a normal lifecycle/backoff state rather than an
# unexpected failure. A mixed cycle is an error as soon as one issue falls
# outside this set.
_WARNING_SYNC_ISSUE_KINDS = frozenset(
    {
        "auth_expired",
        "rate_limited",
        "x_auth_expired",
        "x_rate_limited",
    }
)

_PROFILE_STATUS_KIND_BY_ISSUE = {
    "no_provider": "no_provider",
    "model_not_found": "model_not_found",
    "quota_exhausted": "llm_quota_exhausted",
    "rate_limited": "rate_limited",
    "auth_failed": "llm_auth_failed",
    "timeout": "profile_analysis_timeout",
    "connection": "llm_connection",
    "ssl": "llm_ssl",
    "server_error": "llm_server_error",
    "invalid_response": "llm_invalid_response",
    "moderation": "llm_moderation",
    "unexpected_error": "profile_analysis_error",
}


class SupportsRecentEventUrls(Protocol):
    def recent_event_urls(
        self,
        event_types: list[str],
        *,
        within_hours: int,
        exclude_source: str | None = ...,
        limit: int = ...,
    ) -> set[str]: ...

    def mark_items_seen(self, source_platform: str, content_ids: Iterable[str]) -> int: ...


class SupportsXClient(Protocol):
    async def likes(self, *, limit: int) -> list[dict[str, Any]]: ...
    async def bookmarks(self, *, limit: int) -> list[dict[str, Any]]: ...


class SupportsXHealth(Protocol):
    def is_ready(self) -> bool: ...
    def get(self) -> dict[str, Any]: ...
    def record_success(self, *, strategy: str = "") -> None: ...
    def record_error(self, exc: BaseException, *, strategy: str = "") -> str: ...


class SupportsAccountSyncState(Protocol):
    def load_account_sync_state(self) -> dict[str, object]: ...
    def save_account_sync_state(self, state: dict[str, object]) -> None: ...
    async def propagate_event(self, event: dict[str, Any]) -> None: ...


class SupportsAccountClient(Protocol):
    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]: ...
    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
        max_total_items: int | None = None,
    ) -> list[Any]: ...
    async def get_following(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> list[Any]: ...


def _client_is_authenticated(client: Any) -> bool:
    """True when the client either has no auth concept or reports authed.

    Tests pass plain stubs that don't expose ``is_authenticated``; for
    those, we conservatively assume "authenticated" so behavior matches
    pre-v0.3.57. Production ``BilibiliAPIClient`` exposes the real flag,
    which is what gates the cookie-race short-circuit.
    """
    if not hasattr(client, "is_authenticated"):
        return True
    return bool(client.is_authenticated)


class SupportsSoulAnalyzer(Protocol):
    async def analyze_events(self, events: list[dict[str, Any]]) -> None: ...


@dataclass
class AccountSyncService:
    """Incrementally import account-side history, favorites, and following."""

    memory_manager: SupportsAccountSyncState
    bilibili_client: SupportsAccountClient
    soul_engine: SupportsSoulAnalyzer
    sync_interval_hours: int = 6
    history_max_items: int = 200
    # v0.3.x: favorites budget parity with ``openbiliclaw init``. Folders 11+
    # and follows 101+ used to silently never sync. ``max_total_items`` bounds
    # the worst case at ~1 + ceil(500/20) requests instead of 200×3.
    max_folders: int = 200
    # Per-folder cap matches the total budget, as init's does. At 50 a user
    # whose 默认收藏夹 holds 800 videos had 750 of them invisible to the seen
    # ledger no matter how often sync ran — the comment above claimed parity
    # with init while the numbers said otherwise. ``max_total_items`` still
    # bounds the worst case (~1 + 500/20 requests), so depth costs nothing.
    max_items_per_folder: int = 500
    max_total_items: int = 500
    following_page_size: int = 100
    following_max_pages: int = 5
    check_interval_seconds: int = 300
    llm_work_allowed: Callable[[], bool] | None = None
    database: SupportsRecentEventUrls | None = None
    # Bound by the API/OpenClaw composition root. Kept optional so third-party
    # embedders with the pre-ingress protocol retain a compatibility path.
    event_ingress: Any = None
    x_client: SupportsXClient | None = None
    x_health_store: SupportsXHealth | None = None
    profile_analysis_timeout_seconds: float = DEFAULT_PROFILE_ANALYSIS_TIMEOUT_SECONDS
    _auto_bootstrap_attempted: bool = False
    # v0.3.57+: tracks the cookie-not-ready → ready transition so
    # ``sync_if_due`` only emits the "auth ready" INFO log once per
    # session. Reset path is via fresh AccountSyncService instance,
    # which is what ``rebuild_from_config`` already produces.
    _last_seen_authenticated: bool = False
    # Shared in-flight sync so overlapping callers reuse one result instead of
    # queueing a second, redundant run behind the first.
    _inflight_sync: asyncio.Task[dict[str, object]] | None = None

    async def sync_if_due(self) -> dict[str, object]:
        """Run one account sync only when the configured interval has elapsed."""
        # v0.3.57+: skip the throttle check entirely while the cookie
        # hasn't arrived. ``sync_now`` will short-circuit too — checking
        # here just keeps the no-auth signal visible in run_forever logs
        # without "not_due" noise on every tick of the 5-min poll loop.
        authed = _client_is_authenticated(self.bilibili_client)
        if not authed:
            return {
                "synced": False,
                "new_event_count": 0,
                "reason": "no_auth",
            }
        # Keep the first authenticated tick untouched while guided init owns
        # the bootstrap path (or before the first complete profile exists).
        # Besides avoiding the fetch, this must run before the transition log
        # and flag update so a paused tick cannot falsely announce that the
        # first history fetch is about to start.
        if self.llm_work_allowed is not None and not self.llm_work_allowed():
            # The fetch stages stay paused (guided init owns the bootstrap
            # path), but a persisted ``auth_expired`` must still be re-checked
            # against the live cookie — otherwise a cookie fixed mid-pause
            # leaves the stale banner up until the first tick after unpause.
            await self._refresh_auth_state_while_paused()
            return {
                "synced": False,
                "new_event_count": 0,
                "reason": "llm_paused",
            }
        if not self._last_seen_authenticated:
            self._last_seen_authenticated = True
            logger.info(
                "account_sync: bilibili cookie now ready — first history "
                "fetch will run on this tick"
            )
        state = self.memory_manager.load_account_sync_state()
        if not self._is_due(str(state.get("last_account_sync_at", ""))):
            return {
                "synced": False,
                "new_event_count": 0,
                "reason": "not_due",
            }
        # Re-read the cursor after joining the single-flight: a concurrent sync
        # may have finished between the check above and our turn, which would
        # otherwise make this tick sync again immediately.
        inflight = self._inflight_sync
        if inflight is not None and not inflight.done():
            await asyncio.shield(inflight)
            state = self.memory_manager.load_account_sync_state()
            if not self._is_due(str(state.get("last_account_sync_at", ""))):
                return {
                    "synced": False,
                    "new_event_count": 0,
                    "reason": "not_due",
                }
        return await self.sync_now()

    async def sync_now(self) -> dict[str, object]:
        """Run one immediate incremental account sync.

        Single-flight in two layers. Within the process, overlapping callers
        await the same task rather than each running a sync; across processes
        (the API daemon and an OpenClaw adapter each build their own service
        over the same data dir) a non-blocking OS lock makes the loser return
        ``already_running``. Without this, both could load the same state and
        the last writer would clobber the other's cursors and error kind.
        """
        inflight = self._inflight_sync
        if inflight is not None and not inflight.done():
            return await asyncio.shield(inflight)
        task = asyncio.ensure_future(self._sync_now_guarded())
        self._inflight_sync = task
        try:
            return await asyncio.shield(task)
        finally:
            if self._inflight_sync is task:
                self._inflight_sync = None

    async def _sync_now_guarded(self) -> dict[str, object]:
        """Hold the cross-process run lock for one sync, if it is free."""
        lock_path = self._run_lock_path()
        if lock_path is None:
            return await self._sync_now_locked()
        from openbiliclaw.memory.json_state import exclusive_file_lock

        with exclusive_file_lock(lock_path, blocking=False) as acquired:
            if not acquired:
                logger.info("account_sync: another process holds the run lock; skipping")
                return {
                    "synced": False,
                    "new_event_count": 0,
                    "reason": "already_running",
                }
            return await self._sync_now_locked()

    def _run_lock_path(self) -> Path | None:
        """Sit beside the state file so every process resolves the same path."""
        state_path = getattr(self.memory_manager, "_account_sync_state_path", None)
        if state_path is None:
            return None
        return Path(state_path).with_name("account_sync.run.lock")

    async def _sync_now_locked(self) -> dict[str, object]:
        # v0.3.57+: cookie race short-circuit. Daemon often starts before
        # the extension cookie sync arrives; without this gate, the first
        # tick fetches with empty cookies, gets 0 items, stamps
        # last_account_sync_at, and locks the next attempt out for
        # ``sync_interval_hours`` (default 6h). Bail out before touching
        # the network OR the timestamp so the next ``sync_if_due`` tick
        # (5 min) still re-tries.
        if not _client_is_authenticated(self.bilibili_client):
            return {
                "synced": False,
                "new_event_count": 0,
                "reason": "no_auth",
            }
        state = self.memory_manager.load_account_sync_state()
        events: list[dict[str, Any]] = []
        errors: list[str] = []
        issues: list[dict[str, str]] = []
        error_kind = ""

        try:
            history = await self.bilibili_client.get_user_history(max_items=self.history_max_items)
            previous_history_view_at = self._to_int(state.get("last_history_view_at", 0))
            previous_history_bvids = self._string_set(
                state.get("history_bvids_at_last_view_at", [])
            )
            new_history, last_view_at, last_bvid = self._filter_new_history(
                history,
                last_view_at=previous_history_view_at,
                last_bvid=str(state.get("last_history_bvid", "")),
                seen_bvids_at_last_view_at=previous_history_bvids,
            )
            # No cursor yet ⇒ this is the account's back catalogue, not a
            # behaviour that just happened. The flag travels with the events so
            # the cognition lane can present it as inventory instead of
            # treating a whole viewing history as one afternoon.
            history_is_first_import = previous_history_view_at <= 0
            events.extend(self._history_events(new_history, snapshot=history_is_first_import))
            state["last_history_view_at"] = last_view_at
            state["last_history_bvid"] = last_bvid
            state["history_bvids_at_last_view_at"] = self._history_cursor_bvids(
                history,
                last_view_at,
                fallback_bvid=last_bvid,
                previous_seen=(
                    previous_history_bvids if last_view_at == previous_history_view_at else set()
                ),
            )
        except Exception as exc:
            errors.append(str(exc))
            error_kind = self._record_stage_error(
                "bilibili_history",
                exc,
                error_kind,
                issues,
            )

        try:
            favorites = await self.bilibili_client.get_all_favorites(
                max_folders=self.max_folders,
                max_items_per_folder=self.max_items_per_folder,
                max_total_items=self.max_total_items,
            )
            current_signature = self._favorite_signature(favorites)
            previous_signature = str(state.get("favorite_signature", ""))
            previous_bvids = self._favorite_bvids_from_state(state)
            # Everything in the folder is content the user has seen, whether or
            # not this sync turns it into an event. Only *newly added*
            # favourites become events, so without this the entire back
            # catalogue — favourited before install, or before favourites
            # carried an identity — would stay recommendable forever. Marking
            # from the snapshot is idempotent and adds no preference signal, so
            # it can't double-count what the events already recorded.
            self._mark_favorites_seen(self._favorite_bvids(favorites))
            if current_signature and current_signature != previous_signature:
                new_favorites = self._filter_favorite_folders(favorites, previous_bvids)
                events.extend(
                    self._favorite_events(
                        new_favorites,
                        snapshot=not str(state.get("last_favorites_sync_at") or "").strip(),
                    )
                )
                state["favorite_signature"] = current_signature
                state["favorite_bvids"] = self._favorite_bvids(favorites)
                state["last_favorites_sync_at"] = self._now().isoformat()
            elif current_signature and not state.get("favorite_bvids"):
                state["favorite_bvids"] = self._favorite_bvids(favorites)
        except Exception as exc:
            errors.append(str(exc))
            error_kind = self._record_stage_error(
                "bilibili_favorites",
                exc,
                error_kind,
                issues,
            )

        # Following is paginated (page_size=100, hard cap following_max_pages)
        # so follows past position 100 finally sync. On a mid-loop page failure
        # the pages fetched so far are still ingested; the ``following_mids``
        # set-diff makes the next sync re-cover anything missed.
        following, following_error = await self._collect_following()
        current_signature = self._following_signature(following)
        previous_signature = str(state.get("following_signature", ""))
        previous_mids = self._following_mids_from_state(state)
        if current_signature and current_signature != previous_signature:
            new_following = self._filter_following(following, previous_mids)
            # Without this flag the first import's whole following list reads
            # as "followed N creators today" — the exact misread that pushed
            # personality dimensions from an import artefact.
            events.extend(
                self._following_events(
                    new_following,
                    snapshot=not str(state.get("last_following_sync_at") or "").strip(),
                )
            )
            state["following_signature"] = current_signature
            state["following_mids"] = self._following_mids(following)
            state["last_following_sync_at"] = self._now().isoformat()
        elif current_signature and not state.get("following_mids"):
            state["following_mids"] = self._following_mids(following)
        if following_error is not None:
            errors.append(str(following_error))
            error_kind = self._record_stage_error(
                "bilibili_following",
                following_error,
                error_kind,
                issues,
            )

        if self.x_client is not None:
            error_kind = await self._sync_x(state, events, errors, issues, error_kind)

        if events:
            events = self._dedup_cross_source(events)

        readiness: bool | None = None
        generic_async_owner = False
        if events:
            readiness = self._profile_readiness()
            generic_async_owner = self.event_ingress is not None and readiness is True
            if self.event_ingress is not None:
                await self._persist_via_event_ingress(
                    events,
                    generic_owner=generic_async_owner,
                )
            else:
                # Compatibility-only persistence for older injected embedders.
                for event in events:
                    await self.memory_manager.propagate_event(event)
        if events and not generic_async_owner:
            try:
                timeout = (
                    self.profile_analysis_timeout_seconds
                    if self.profile_analysis_timeout_seconds > 0
                    else None
                )
                async with asyncio.timeout(timeout):
                    await self._apply_legacy_profile_update(
                        events,
                        readiness=readiness,
                    )
            except TimeoutError as exc:
                timeout_error = TimeoutError(_PROFILE_ANALYSIS_TIMEOUT_MESSAGE)
                logger.warning(
                    "Profile analysis timed out during account sync after %.1fs",
                    self.profile_analysis_timeout_seconds,
                )
                self._persist_profile_analysis_error(
                    str(timeout_error),
                    kind="profile_analysis_timeout",
                    issues=issues,
                    issue_kind="timeout",
                )
                raise timeout_error from exc
            except Exception as exc:
                # A profile-analysis failure here is almost always the chat
                # LLM being unavailable (a local model never pulled → 404, a
                # gateway rejecting auth → 401, or a timeout). Historically
                # this was a bare await: the exception bubbled straight up,
                # run_forever logged one backend line, but the user-visible
                # last_sync_error was never written — so guided init just
                # showed an endless wait with nothing to diagnose (CLAUDE.md
                # pitfall #7: failures must be diagnosable). Record the reason
                # in last_sync_error WITHOUT advancing any sync cursor (the
                # whole tick still rolls back and retries next cycle), then
                # re-raise so run_forever still classifies/logs it. CancelledError
                # subclasses BaseException, so hot-reload/restart cancellation
                # is not swallowed here.
                logger.warning(
                    "Profile analysis failed during account sync: %s", exc, exc_info=True
                )
                safe_message = safe_llm_failure_message(exc)
                profile_issue_kind = self._classify_profile_issue_kind(
                    exc,
                    safe_message,
                )
                self._persist_profile_analysis_error(
                    safe_message,
                    kind=_PROFILE_STATUS_KIND_BY_ISSUE.get(
                        profile_issue_kind,
                        "profile_analysis_error",
                    ),
                    issues=issues,
                    issue_kind=profile_issue_kind,
                )
                raise

        state["last_account_sync_at"] = self._now().isoformat()
        state["last_sync_error"] = self._merge_stage_errors(errors)
        state["last_sync_error_kind"] = error_kind
        state["last_sync_issues"] = issues
        self.memory_manager.save_account_sync_state(state)
        return {
            "synced": bool(events),
            "new_event_count": len(events),
            "errors": errors,
        }

    @staticmethod
    def _merge_stage_errors(errors: list[str]) -> str:
        """Join stage errors, dropping repeats of the same message.

        One expired cookie fails several stages: the history stage hits
        /x/web-interface/history/cursor, while favorites and following each
        call get_nav_info() for the mid. All three raised the same -101, so
        the joined string repeated one cause three times.
        """
        merged: list[str] = []
        for error in errors:
            message = error.strip()
            if message and message not in merged:
                merged.append(message)
        return " | ".join(merged)

    @classmethod
    def _record_stage_error(
        cls,
        stage: str,
        exc: Exception,
        current_kind: str,
        issues: list[dict[str, str]] | None = None,
    ) -> str:
        """Log a swallowed fetch-stage error and classify it.

        Expected X failures keep their platform identity instead of collapsing
        into the generic ``error`` used for unrelated fetch failures. This is
        what lets the status surface explain that an X-only 429 did not break
        Bilibili account sync. Higher-priority actionable states still win when
        more than one stage fails in the same cycle.
        """
        logger.warning("account_sync: %s fetch failed: %s", stage.replace("_", " "), exc)
        issue_kind = cls._classify_stage_issue(stage, exc)
        if issues is not None:
            cls._append_sync_issue(issues, stage=stage, kind=issue_kind)
        candidate = (
            issue_kind
            if issue_kind in {"auth_expired", "x_auth_expired", "x_blocked", "x_rate_limited"}
            else "error"
        )
        return cls._prefer_error_kind(current_kind, candidate)

    @classmethod
    def _classify_stage_issue(cls, stage: str, exc: BaseException) -> str:
        """Return a safe reason code for one source-fetch failure."""
        if isinstance(exc, BilibiliAuthExpiredError):
            return "auth_expired"
        if isinstance(exc, (XMissingCookieError, XAuthError)):
            return "x_auth_expired"
        if isinstance(exc, XBlockedError):
            return "x_blocked"
        if isinstance(exc, (XRateLimitError, XClientError)):
            return "x_rate_limited"

        chain = cls._exception_chain(exc)
        if any(
            isinstance(item, (TimeoutError, httpx.TimeoutException))
            or (
                isinstance(item, OSError)
                and item.errno
                in {
                    errno.ETIMEDOUT,
                }
            )
            for item in chain
        ):
            return "timeout"
        if any(
            isinstance(item, (ConnectionError, httpx.NetworkError))
            or (
                isinstance(item, OSError)
                and item.errno
                in {
                    errno.ECONNABORTED,
                    errno.ECONNREFUSED,
                    errno.ECONNRESET,
                    errno.ENETDOWN,
                    errno.ENETUNREACH,
                    errno.EHOSTDOWN,
                    errno.EHOSTUNREACH,
                }
            )
            for item in chain
        ):
            return "network"
        if stage.startswith("bilibili_"):
            bili_error = next(
                (item for item in chain if isinstance(item, BilibiliAPIError)),
                None,
            )
            if bili_error is not None:
                code = getattr(bili_error, "code", None)
                if isinstance(code, int) and abs(code) in {412, 429}:
                    return "rate_limited"
                if "rate limit" in str(bili_error).lower():
                    return "rate_limited"
                return "api_error"
        return "unexpected_error"

    @staticmethod
    def _exception_chain(exc: BaseException) -> list[BaseException]:
        """Return an exception chain without following cycles."""
        chain: list[BaseException] = []
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            chain.append(current)
            current = current.__cause__ or current.__context__
        return chain

    @classmethod
    def _append_sync_issue(
        cls,
        issues: list[dict[str, str]],
        *,
        stage: str,
        kind: str,
    ) -> None:
        """Append one bounded, de-duplicated machine-readable sync issue."""
        if len(issues) >= _MAX_SYNC_ISSUES:
            return
        normalized_stage = stage if stage in _SYNC_STAGE_LABELS else "unknown"
        normalized_kind = kind if kind in _SYNC_ISSUE_KINDS else "unexpected_error"
        issue = {"stage": normalized_stage, "kind": normalized_kind}
        if issue not in issues:
            issues.append(issue)

    @staticmethod
    def _classify_profile_issue_kind(exc: BaseException, safe_message: str) -> str:
        """Preserve actionable distinctions absent from the broad classifier."""
        if "内容合规策略拒绝" in safe_message:
            return "moderation"
        if "SSL 证书验证失败" in safe_message:
            return "ssl"
        chain_text = " ".join(
            str(item).lower() for item in AccountSyncService._exception_chain(exc)
        )
        if any(
            marker in chain_text
            for marker in ("insufficient_quota", "insufficient quota", "quota exhausted")
        ):
            return "quota_exhausted"
        return classify_llm_failure_kind(exc) or "unexpected_error"

    @staticmethod
    def _prefer_error_kind(current_kind: str, candidate: str) -> str:
        """Return the most actionable error kind observed in this sync cycle."""
        priority = {
            "": 0,
            "x_rate_limited": 1,
            "x_blocked": 2,
            "x_auth_expired": 3,
            # A non-X stage also failed, so X-only copy must not claim that
            # Bilibili and every other source were unaffected.
            "error": 4,
            "auth_expired": 5,
        }
        current = str(current_kind or "")
        new = str(candidate or "")
        return new if priority.get(new, 2) > priority.get(current, 2) else current

    async def _persist_via_event_ingress(
        self,
        events: list[dict[str, Any]],
        *,
        generic_owner: bool,
    ) -> None:
        """Commit account facts; only ready profiles opt into async ownership."""
        canonical: list[dict[str, Any]] = []
        for raw_event in events:
            event = dict(raw_event)
            metadata = event.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, dict) else {}
            metadata.setdefault("event_namespace", "import")
            if generic_owner:
                metadata["profile_update_owner"] = "generic"
            else:
                metadata.pop("profile_update_owner", None)
            event["metadata"] = metadata
            identity_parts = (
                str(event.get("event_type") or event.get("type") or ""),
                str(event.get("url") or ""),
                str(metadata.get("source_platform") or event.get("source_platform") or ""),
                str(
                    metadata.get("content_id")
                    or metadata.get("bvid")
                    or metadata.get("mid")
                    or metadata.get("tweet_id")
                    or ""
                ),
                str(metadata.get("action") or metadata.get("timestamp") or ""),
            )
            digest = hashlib.sha256("\x1f".join(identity_parts).encode()).hexdigest()
            event["ingest_key"] = digest
            canonical.append(event)
        receipt = await self.event_ingress.accept_batch(
            canonical,
            producer="account-sync",
        )
        if receipt.rejected:
            reasons = "; ".join(item.error for item in receipt.items if item.error)
            raise RuntimeError(f"account-sync event ingress rejected event: {reasons}")

    async def _apply_legacy_profile_update(
        self,
        events: list[dict[str, Any]],
        *,
        readiness: bool | None,
    ) -> None:
        """Compatibility analyzer for embedders that have not bound ingress."""
        if readiness is True:
            if await self._try_pipeline_ingest(events):
                return
            logger.warning(
                "account_sync: profile pipeline unavailable/failed; falling back to analyze_events"
            )
            await self.soul_engine.analyze_events(events)
            return

        # Not ready (or readiness unknown) → legacy analyze_events. Bootstrap
        # only when readiness is *definitively* False (a profile can still be
        # built); unknown readiness stays conservative and skips the bootstrap.
        await self.soul_engine.analyze_events(events)
        if readiness is False:
            await self._auto_bootstrap_soul_profile(len(events))

    async def _try_pipeline_ingest(self, events: list[dict[str, Any]]) -> bool:
        """Feed a ready profile through the pre-ingress pipeline contract.

        This path is compatibility-only: composition roots that bind
        ``event_ingress`` use the durable generic event owner instead.  A
        missing or failing narrow pipeline returns ``False`` so older
        embedders retain their documented ``analyze_events`` fallback.
        """
        pipeline = getattr(self.soul_engine, "pipeline", None)
        if pipeline is None:
            return False
        ingest_batch = getattr(pipeline, "ingest_batch", None)
        if not callable(ingest_batch):
            return False
        try:
            from openbiliclaw.soul.pipeline import signals_from_events

            await ingest_batch(signals_from_events(events))
            return True
        except Exception:
            logger.warning("account_sync: profile pipeline ingest failed", exc_info=True)
            return False

    def _profile_readiness(self) -> bool | None:
        """Return profile readiness, or ``None`` when it cannot be determined.

        ``None`` (``is_profile_ready`` missing or raising) is treated as *not
        ready* for routing but skips the first-profile bootstrap, preserving
        the original conservative behavior.
        """
        is_ready = getattr(self.soul_engine, "is_profile_ready", None)
        if not callable(is_ready):
            return None
        try:
            return bool(is_ready())
        except Exception:
            logger.debug("account_sync: is_profile_ready check failed", exc_info=True)
            return None

    def _dedup_cross_source(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop events whose identity key already appears in the events table.

        Guards against double-counting an observation that the browser
        extension already recorded (extension ``view`` + history ``view`` for
        the same bvid, etc.). Watermark/cursor state is advanced by the caller
        before this runs, so a deduped item never stalls the cursor. Own prior
        rows are excluded via ``exclude_source="account_sync"`` so a re-watch
        seen only via the pulled API still flows.
        """
        if self.database is None:
            return events

        recent_keys_by_type: dict[str, set[str]] = {}
        for event_type in {str(event.get("event_type", "")) for event in events}:
            if not event_type:
                continue
            try:
                recent_urls = self.database.recent_event_urls(
                    [event_type],
                    within_hours=_CROSS_SOURCE_DEDUP_WINDOW_HOURS,
                    exclude_source="account_sync",
                )
            except Exception as exc:
                logger.warning(
                    "account_sync: cross-source dedup lookup for %s failed: %s",
                    event_type,
                    exc,
                )
                recent_urls = set()
            recent_keys_by_type[event_type] = {
                key for url in recent_urls if (key := _dedup_key(url))
            }

        kept: list[dict[str, Any]] = []
        dropped_counts: dict[str, int] = {}
        for event in events:
            event_type = str(event.get("event_type", ""))
            key = _dedup_key(str(event.get("url", "")))
            if key and key in recent_keys_by_type.get(event_type, set()):
                dropped_counts[event_type] = dropped_counts.get(event_type, 0) + 1
                continue
            kept.append(event)

        if dropped_counts:
            summary = " / ".join(
                f"{count} {event_type}" for event_type, count in sorted(dropped_counts.items())
            )
            logger.info("account_sync: deduped %s events already observed by extension", summary)
        return kept

    async def _sync_x(
        self,
        state: dict[str, Any],
        events: list[dict[str, Any]],
        errors: list[str],
        issues: list[dict[str, str]],
        error_kind: str,
    ) -> str:
        """Fetch X likes/bookmarks server-side and append incremental events.

        Read-only, one fetch pair per cycle, set-based dedup on normalized tweet
        ids. First sync (empty state sets) seeds from tweet ids already persisted
        in the events table so a like made between init and the first cycle is
        NOT silently swallowed. Failures feed the shared error list + WARN log
        (Task 4) and never block the bilibili sync.
        """
        if self.x_client is None:
            return error_kind

        blocked_state = self._x_unready_state()
        if blocked_state:
            message, kind = self._x_health_skip(blocked_state)
            errors.append(message)
            self._append_sync_issue(issues, stage="x_preferences", kind=kind)
            logger.info("account_sync: X likes/bookmarks skipped: source health=%s", blocked_state)
            return self._prefer_error_kind(error_kind, kind)

        like_ids = self._string_list(state.get("x_like_ids", []))
        bookmark_ids = self._string_list(state.get("x_bookmark_ids", []))
        if not like_ids and not bookmark_ids:
            seed = self._seed_x_ids_from_events()
            like_ids = list(seed)
            bookmark_ids = list(seed)

        try:
            likes = await self.x_client.likes(limit=_X_FETCH_LIMIT)
            like_events, like_ids = self._x_ingest(likes, event_type="like", seen_ids=like_ids)
            events.extend(like_events)
            state["x_like_ids"] = like_ids
            self._record_x_health_success("likes")
        except Exception as exc:
            errors.append(str(exc))
            self._record_x_health_error(exc, "likes")
            error_kind = self._record_stage_error("x_likes", exc, error_kind, issues)

        # A typed failure above may just have opened the shared source cooldown.
        # Do not immediately spend a second request on bookmarks and defeat the
        # backoff that the X discovery producer and status card both promise.
        if self._x_unready_state():
            return error_kind

        try:
            bookmarks = await self.x_client.bookmarks(limit=_X_FETCH_LIMIT)
            bookmark_events, bookmark_ids = self._x_ingest(
                bookmarks, event_type="favorite", seen_ids=bookmark_ids
            )
            events.extend(bookmark_events)
            state["x_bookmark_ids"] = bookmark_ids
            self._record_x_health_success("bookmarks")
        except Exception as exc:
            errors.append(str(exc))
            self._record_x_health_error(exc, "bookmarks")
            error_kind = self._record_stage_error("x_bookmarks", exc, error_kind, issues)

        return error_kind

    def _x_unready_state(self) -> str:
        """Return the shared X health state when it currently forbids a request."""
        if self.x_health_store is None:
            return ""
        try:
            if self.x_health_store.is_ready():
                return ""
            return str(self.x_health_store.get().get("state", "") or "")
        except Exception:
            # Health persistence is a guardrail, not a new single point of
            # failure. Preserve the pre-health-store fetch path if its read fails.
            logger.warning("account_sync: failed to read X source health", exc_info=True)
            return ""

    @staticmethod
    def _x_health_skip(state: str) -> tuple[str, str]:
        """Map a persisted X health block to safe diagnostics and display kind."""
        if state == "rate_limited":
            return (
                "X 账号喜好同步因来源限流冷却而跳过",
                "x_rate_limited",
            )
        if state in {"missing_cookie", "expired_cookie"}:
            return (
                "X 账号喜好同步因登录凭据缺失或失效而跳过",
                "x_auth_expired",
            )
        if state == "blocked":
            return (
                "X 账号喜好同步因请求被拒绝而跳过",
                "x_blocked",
            )
        return (f"X 账号喜好同步因来源状态 {state or 'unknown'} 而跳过", "error")

    def _record_x_health_success(self, strategy: str) -> None:
        if self.x_health_store is None:
            return
        try:
            self.x_health_store.record_success(strategy=strategy)
        except Exception:
            logger.warning("account_sync: failed to record X source success", exc_info=True)

    def _record_x_health_error(self, exc: BaseException, strategy: str) -> None:
        if self.x_health_store is None:
            return
        try:
            self.x_health_store.record_error(exc, strategy=strategy)
        except Exception:
            logger.warning("account_sync: failed to record X source error", exc_info=True)

    def _seed_x_ids_from_events(self) -> list[str]:
        """Seed the seen-tweet-id set from persisted X ``like``/``favorite`` events."""
        if self.database is None:
            return []
        try:
            urls = self.database.recent_event_urls(
                ["like", "favorite"],
                within_hours=_X_SEED_WINDOW_HOURS,
                exclude_source=None,
                limit=_X_ID_CAP,
            )
        except Exception as exc:
            logger.warning("account_sync: X first-sync seed lookup failed: %s", exc)
            return []
        seeded: list[str] = []
        for url in urls:
            if "x.com" not in url and "twitter.com" not in url:
                continue
            tweet_id = _tweet_id_from_url(url)
            if tweet_id:
                seeded.append(tweet_id)
        return seeded

    def _x_ingest(
        self,
        tweets: list[dict[str, Any]],
        *,
        event_type: str,
        seen_ids: list[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Build events for tweets whose id is unseen; return (events, capped ids)."""
        seen = set(seen_ids)
        emitted: list[dict[str, Any]] = []
        fetched_ids: list[str] = []
        for tweet in tweets:
            if not isinstance(tweet, dict):
                continue
            tweet_id = str(tweet.get("id", "") or "").strip()
            if not tweet_id:
                continue
            fetched_ids.append(tweet_id)
            if tweet_id in seen:
                continue
            event = self._x_event(tweet, event_type=event_type)
            if event is not None:
                emitted.append(event)
        return emitted, self._cap_x_ids(fetched_ids, seen_ids)

    @staticmethod
    def _cap_x_ids(fetched_newest_first: list[str], existing: list[str]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for tweet_id in [*fetched_newest_first, *existing]:
            if tweet_id and tweet_id not in seen:
                seen.add(tweet_id)
                ordered.append(tweet_id)
        return ordered[:_X_ID_CAP]

    @staticmethod
    def _x_event(tweet: dict[str, Any], *, event_type: str) -> dict[str, Any] | None:
        """Normalize a ``tweet_to_dict`` payload into a unified account_sync event.

        Mirrors ``cli._x_tweet_to_event`` (init backfill) so scheduled and init
        X events share one shape. ``source_platform`` is the canonical
        ``"twitter"`` — never ``"x"``, which would split source-mix accounting.
        """
        from openbiliclaw.sources.event_format import SOURCE_TWITTER, build_event

        tweet_id = str(tweet.get("id", "") or "").strip()
        if not tweet_id:
            return None
        raw_author = tweet.get("author")
        author = raw_author if isinstance(raw_author, dict) else {}
        screen_name = str(author.get("screenName", "") or "").strip()
        if screen_name:
            author_name = f"@{screen_name}"
        else:
            author_name = str(author.get("name", "") or "").strip()
        handle = screen_name or "i"  # x.com/i/status/<id> resolves without a handle
        text = str(tweet.get("articleText") or tweet.get("text") or "").strip()
        first_line = text.splitlines()[0] if text else ""
        title = first_line[:120]
        return build_event(
            event_type=event_type,
            source_platform=SOURCE_TWITTER,
            title=title,
            url=f"https://x.com/{handle}/status/{tweet_id}",
            author=author_name,
            metadata={
                "tweet_id": tweet_id,
                "screen_name": screen_name,
                "body_text": text,
                "source": "account_sync",
            },
        )

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for raw in value if (item := str(raw).strip())]

    def _persist_profile_analysis_error(
        self,
        message: str,
        *,
        kind: str,
        issues: list[dict[str, str]],
        issue_kind: str,
    ) -> None:
        """Record a profile-analysis failure without advancing any sync cursor.

        Loads a FRESH state so the failing tick's in-flight cursor bumps
        (history / favorites / following) are not persisted — the tick still
        rolls back and retries next cycle — while ``last_sync_error`` carries a
        user-visible reason to ``/api/init-status`` and the account-sync status
        surface. ``last_account_sync_at`` is deliberately left untouched so the
        throttle does not lock the retry out for ``sync_interval_hours``.

        ``kind`` carries the status-level classification, while ``issues``
        preserves every source stage that already failed in this same tick and
        adds a machine-readable ``profile_analysis`` reason. This lets the UI
        explain a timeout, provider auth failure, connection failure, or invalid
        response without showing raw provider text.
        """
        try:
            state = self.memory_manager.load_account_sync_state()
            current_issues = list(issues)
            self._append_sync_issue(
                current_issues,
                stage="profile_analysis",
                kind=issue_kind,
            )
            state["last_sync_error"] = f"画像分析失败：{message}"
            state["last_sync_error_kind"] = kind
            state["last_sync_issues"] = current_issues
            self.memory_manager.save_account_sync_state(state)
        except Exception:
            logger.debug("Failed to persist profile-analysis error", exc_info=True)

    def clear_auth_expired_state(self) -> bool:
        """Drop persisted ``auth_expired`` state once its cause is gone.

        Called when a fresh bilibili cookie lands or a live re-check finds
        the current cookie logged in again. The next completed tick
        re-derives every issue from real fetch results, so clearing here
        can only remove a *stale* banner, never a true one. Returns True
        when persisted state changed.
        """
        state = self.memory_manager.load_account_sync_state()
        issues = self._normalize_sync_issues(state.get("last_sync_issues", []))
        kept = [issue for issue in issues if issue["kind"] != "auth_expired"]
        changed = len(kept) != len(issues)
        if str(state.get("last_sync_error_kind", "")) == "auth_expired":
            state["last_sync_error"] = ""
            state["last_sync_error_kind"] = ""
            changed = True
        if not changed:
            return False
        state["last_sync_issues"] = kept
        self.memory_manager.save_account_sync_state(state)
        logger.info("account_sync: cleared persisted auth_expired state (cookie usable again)")
        return True

    async def _refresh_auth_state_while_paused(self) -> None:
        """Re-check a persisted ``auth_expired`` against the live nav endpoint.

        Only the stale-banner case pays the network call: nothing is probed
        while the persisted state carries no ``auth_expired``. A nav answer
        of "logged in" clears the stale state; a definitive expiry, a
        transport failure, or a client without a nav probe all leave it
        untouched (no verdict is no evidence either way).
        """
        state = self.memory_manager.load_account_sync_state()
        kind = str(state.get("last_sync_error_kind", ""))
        issues = self._normalize_sync_issues(state.get("last_sync_issues", []))
        if kind != "auth_expired" and all(issue["kind"] != "auth_expired" for issue in issues):
            return
        probe = getattr(self.bilibili_client, "get_nav_info", None)
        if not callable(probe):
            return
        try:
            nav = await probe()
        except BilibiliAuthExpiredError:
            return
        except Exception:
            logger.debug(
                "account_sync: paused auth re-check could not reach bilibili",
                exc_info=True,
            )
            return
        if bool(getattr(nav, "is_login", False)):
            self.clear_auth_expired_state()

    def get_runtime_status(self) -> dict[str, object]:
        """Expose lightweight account sync runtime fields."""
        state = self.memory_manager.load_account_sync_state()
        kind = str(state.get("last_sync_error_kind", ""))
        detail = str(state.get("last_sync_error", ""))
        issues = self._normalize_sync_issues(state.get("last_sync_issues", []))
        return {
            "last_account_sync_at": str(state.get("last_account_sync_at", "")),
            # Raw provider text — diagnostics only, never the display string.
            "last_account_sync_error": detail,
            "last_account_sync_error_kind": kind,
            # Stable stage/reason pairs let support identify the failed
            # subsystem without parsing or exposing the raw exception.
            "last_account_sync_issues": issues,
            # Display copy lives here so every consuming surface can reuse the
            # same sentence instead of formatting the raw error itself.
            "last_account_sync_message": self._user_facing_sync_message(
                kind,
                detail,
                issues,
            ),
            "last_account_sync_severity": self._sync_issue_severity(
                kind,
                detail,
                issues,
            ),
        }

    @classmethod
    def _normalize_sync_issues(cls, value: object) -> list[dict[str, str]]:
        """Validate persisted issue rows at the runtime boundary."""
        if not isinstance(value, list):
            return []
        issues: list[dict[str, str]] = []
        for raw in value[:_MAX_SYNC_ISSUES]:
            if not isinstance(raw, dict):
                continue
            stage = raw.get("stage")
            kind = raw.get("kind")
            if not isinstance(stage, str) or not isinstance(kind, str):
                continue
            cls._append_sync_issue(issues, stage=stage, kind=kind)
        return issues

    @staticmethod
    def _sync_issue_severity(
        kind: str,
        detail: str,
        issues: list[dict[str, str]],
    ) -> str:
        if issues:
            return (
                "warning"
                if all(issue["kind"] in _WARNING_SYNC_ISSUE_KINDS for issue in issues)
                else "error"
            )
        if kind in ("auth_expired", "rate_limited", "x_auth_expired", "x_rate_limited"):
            return "warning"
        return "error" if detail else ""

    @classmethod
    def _user_facing_sync_message(
        cls,
        kind: str,
        detail: str,
        issues: list[dict[str, str]] | None = None,
    ) -> str:
        """Render the user-visible sentence for a sync error.

        An expired cookie is a normal lifecycle event, not a fault, so it gets
        an actionable instruction rather than the provider's English error.
        LLM-unavailability kinds likewise get actionable copy: "稍后会自动重试"
        would be a false promise for ``no_provider`` / ``model_not_found`` —
        nothing recovers until the user fixes the model configuration.
        """
        normalized_issues = cls._normalize_sync_issues(issues or [])
        if normalized_issues:
            return cls._render_sync_issue_message(normalized_issues)
        if kind == "auth_expired":
            return (
                "B 站登录已失效，账号同步已停止。"
                "请在浏览器重新登录 B 站，或保持扩展在线以同步新的 Cookie。"
            )
        if kind == "no_provider":
            return (
                "AI 模型未配置或不可用，画像同步已暂停 — "
                "请在设置中检查模型配置（API Key 与服务地址），修复后会自动恢复。"
            )
        if kind == "model_not_found":
            return (
                "配置的 AI 模型不存在 — "
                "请在设置中修正模型名（本地模型需先拉取），修复后会自动恢复。"
            )
        if kind == "rate_limited":
            return "AI 服务限流中，账号同步稍后会自动重试。"
        if kind == "llm_quota_exhausted":
            return (
                "账号数据已读取，但 AI 服务额度不足或已用尽，画像未更新。"
                "请检查余额或套餐，补充额度后会自动重试。"
            )
        if kind == "x_rate_limited":
            return (
                "X 暂时限流，已跳过本轮 X 喜好同步；B 站等其他来源不受影响。"
                "冷却结束后会自动重试，无需操作。"
            )
        if kind == "x_auth_expired":
            return (
                "X 登录凭据缺失或已失效，X 喜好同步已暂停；"
                "请在浏览器重新登录 X 并保持扩展在线。B 站等其他来源不受影响。"
            )
        if kind == "x_blocked":
            return (
                "X 拒绝了账号读取请求，已跳过 X 喜好同步；"
                "请在来源设置中查看 X 状态并测试连接。B 站等其他来源不受影响。"
            )
        if kind == "profile_analysis_timeout":
            return (
                "账号数据已读取，但 AI 画像分析超过 6 分钟仍未完成。"
                "请在模型设置中测试服务地址、模型名与网络，修复后会自动重试。"
            )
        if kind == "llm_auth_failed":
            return (
                "账号数据已读取，但 AI 服务鉴权失败，画像未更新。"
                "请在模型设置中检查 API Key，修复后会自动重试。"
            )
        if kind == "llm_connection":
            return (
                "账号数据已读取，但无法连接 AI 服务，画像未更新。"
                "请在模型设置中测试服务地址、网络与代理，修复后会自动重试。"
            )
        if kind == "llm_ssl":
            return (
                "账号数据已读取，但 AI 服务的 SSL 证书验证失败，画像未更新。"
                "请检查代理、防火墙或自签证书配置，修复后会自动重试。"
            )
        if kind == "llm_server_error":
            return (
                "账号数据已读取，但 AI 服务返回服务器错误，画像未更新。"
                "请稍后重试；若持续出现，请在模型设置中测试服务。"
            )
        if kind == "llm_invalid_response":
            return (
                "账号数据已读取，但 AI 服务返回空内容或无法解析的结果，画像未更新。"
                "请更换模型或在模型设置中测试服务。"
            )
        if kind == "llm_moderation":
            return (
                "账号数据已读取，但 AI 服务因内容合规策略拒绝了画像分析。"
                "请更换模型或服务商，修复后会自动重试。"
            )
        if kind == "profile_analysis_error":
            return (
                "账号数据已读取，但 AI 画像分析遇到未分类异常，画像未更新。"
                "请在模型设置中测试服务；若持续出现，请反馈诊断信息。"
            )
        if detail:
            return (
                "账号同步遇到旧版或未分类异常，暂时无法确定具体环节。"
                "已成功的数据已保留；系统会在下一轮重试。"
            )
        return ""

    @classmethod
    def _render_sync_issue_message(cls, issues: list[dict[str, str]]) -> str:
        """Build concise, actionable Chinese copy from structured issues."""
        grouped: dict[tuple[str, str], list[str]] = {}
        for issue in issues:
            stage = issue["stage"]
            reason = issue["kind"]
            if stage.startswith("bilibili_"):
                domain = "bilibili"
            elif stage.startswith("x_"):
                domain = "x"
            elif stage == "profile_analysis":
                domain = "profile"
            else:
                domain = "unknown"
            grouped.setdefault((domain, reason), []).append(stage)

        phrases: list[str] = []
        actions: list[str] = []
        domains = {domain for domain, _reason in grouped}
        has_retryable_issue = False

        for (domain, reason), stages in grouped.items():
            stage_labels = cls._join_stage_labels(stages)
            if domain == "bilibili":
                if reason == "auth_expired":
                    phrase = f"B 站登录已失效，{stage_labels}无法读取"
                    action = "请在浏览器重新登录 B 站，或保持扩展在线同步新的 Cookie。"
                else:
                    reason_label = {
                        "rate_limited": "B 站接口限流",
                        "timeout": "请求超时",
                        "network": "无法连接 B 站",
                        "api_error": "B 站接口返回异常",
                        "unexpected_error": "未分类异常",
                    }.get(reason, "未分类异常")
                    phrase = f"B 站{stage_labels}未同步（{reason_label}）"
                    action = ""
                    has_retryable_issue = True
            elif domain == "x":
                if reason == "x_rate_limited":
                    phrase = f"X 暂时限流，{stage_labels}本轮未同步"
                    action = ""
                    has_retryable_issue = True
                elif reason == "x_auth_expired":
                    phrase = f"X 登录凭据缺失或已失效，{stage_labels}同步已暂停"
                    action = "请在浏览器重新登录 X 并保持扩展在线。"
                elif reason == "x_blocked":
                    phrase = f"X 拒绝了{stage_labels}读取请求"
                    action = "请在来源设置中查看 X 状态并测试连接。"
                else:
                    reason_label = {
                        "timeout": "请求超时",
                        "network": "网络连接失败",
                        "unexpected_error": "未分类异常",
                    }.get(reason, "未分类异常")
                    phrase = f"X {stage_labels}未同步（{reason_label}）"
                    action = ""
                    has_retryable_issue = True
            elif domain == "profile":
                phrase, action, retryable = cls._profile_issue_copy(reason)
                has_retryable_issue = has_retryable_issue or retryable
            else:
                phrase = "账号同步遇到未分类异常"
                action = ""
                has_retryable_issue = True
            if phrase not in phrases:
                phrases.append(phrase)
            if action and action not in actions:
                actions.append(action)

        if not phrases:
            return ""
        if len(phrases) == 1:
            message = f"本轮账号同步：{phrases[0]}。"
        else:
            message = f"本轮账号同步发现 {len(phrases)} 类问题：{'；'.join(phrases)}。"
        if domains == {"x"}:
            message += "B 站等其他来源不受影响。"
        if actions:
            message += "".join(actions)
        if has_retryable_issue:
            if domains == {"x"} and all(issue["kind"] == "x_rate_limited" for issue in issues):
                message += "冷却结束后会自动重试，无需操作。"
            else:
                message += "已成功的环节已保留；系统会在下一轮自动重试。"
        elif actions:
            message += "修复后会自动重试。"
        return message

    @staticmethod
    def _join_stage_labels(stages: list[str]) -> str:
        labels: list[str] = []
        for stage in stages:
            label = _SYNC_STAGE_LABELS.get(stage, "未知环节")
            if label not in labels:
                labels.append(label)
        return "、".join(labels)

    @staticmethod
    def _profile_issue_copy(reason: str) -> tuple[str, str, bool]:
        """Return ``(problem, action, retryable_without_user_action)``."""
        if reason == "no_provider":
            return (
                "AI 模型未配置或不可用，画像未更新",
                "请在模型设置中配置可用服务。",
                False,
            )
        if reason == "model_not_found":
            return (
                "配置的 AI 模型不存在，画像未更新",
                "请在模型设置中修正模型名；本地模型需先拉取。",
                False,
            )
        if reason == "rate_limited":
            return ("AI 服务限流，画像本轮未更新", "", True)
        if reason == "quota_exhausted":
            return (
                "AI 服务额度不足或已用尽，画像未更新",
                "请检查 AI 服务余额或套餐。",
                False,
            )
        if reason == "auth_failed":
            return (
                "AI 服务鉴权失败，画像未更新",
                "请在模型设置中检查 API Key。",
                False,
            )
        if reason == "timeout":
            return (
                "账号数据已读取，但 AI 画像分析超过 6 分钟仍未完成",
                "请在模型设置中测试服务地址、模型名与网络。",
                False,
            )
        if reason == "connection":
            return (
                "账号数据已读取，但无法连接 AI 服务，画像未更新",
                "请在模型设置中测试服务地址、网络与代理。",
                False,
            )
        if reason == "ssl":
            return (
                "AI 服务的 SSL 证书验证失败，画像未更新",
                "请检查代理、防火墙或自签证书配置。",
                False,
            )
        if reason == "server_error":
            return ("AI 服务返回服务器错误，画像未更新", "", True)
        if reason == "invalid_response":
            return (
                "AI 服务返回空内容或无法解析的结果，画像未更新",
                "请更换模型或在模型设置中测试服务。",
                False,
            )
        if reason == "moderation":
            return (
                "AI 服务因内容合规策略拒绝了画像分析",
                "请更换模型或服务商。",
                False,
            )
        return (
            "AI 画像分析遇到未分类异常，画像未更新",
            "请在模型设置中测试服务；若持续出现，请反馈诊断信息。",
            False,
        )

    async def _auto_bootstrap_soul_profile(self, event_count: int) -> None:
        """Build the first soul profile after account sync learns preferences.

        The caller (:meth:`_apply_legacy_profile_update`) only reaches this when the
        profile is *definitively* not ready, so readiness is no longer
        re-probed here — that keeps ``is_profile_ready`` to one call per sync.
        """
        if self._auto_bootstrap_attempted:
            return

        build_candidate = getattr(self.soul_engine, "build_initial_profile", None)
        if not callable(build_candidate):
            self._auto_bootstrap_attempted = True
            return
        build_fn = cast("Callable[[list[dict[str, Any]]], Awaitable[Any]]", build_candidate)

        self._auto_bootstrap_attempted = True
        try:
            logger.info(
                "Auto-bootstrapping soul profile after account sync (%d new events)",
                event_count,
            )
            await build_fn([])
        except Exception:
            logger.warning(
                "Auto-bootstrap of soul profile failed; run 'openbiliclaw init' "
                "manually for a richer profile",
                exc_info=True,
            )

    # v0.3.57+: tighter retry while cookie hasn't arrived. The default
    # ``check_interval_seconds`` of 300 is right for steady-state polling
    # but stretches the cookie-race symptom — daemon up, cookie arrives
    # ~2s later, but next history fetch waits up to 5 min. Drop to 15s
    # until first auth, restore to ``check_interval_seconds`` after.
    _UNAUTH_RETRY_INTERVAL_SECONDS: ClassVar[int] = 15

    async def run_forever(self) -> None:
        """Run account sync loop until cancelled."""
        while True:
            authed_before = self._last_seen_authenticated
            try:
                await self.sync_if_due()
            except Exception as exc:
                kind = classify_llm_unavailability(exc)
                if kind == "no_provider":
                    logger.info(
                        "account sync skipped: no chat LLM provider configured yet "
                        "(retry next cycle)"
                    )
                elif kind == "model_not_found":
                    logger.warning(
                        "account sync deferred: configured chat model not found "
                        "(pull the local model or fix the model name); retry next cycle"
                    )
                elif kind == "rate_limited":
                    logger.warning(
                        "account sync deferred: LLM provider rate-limited/cooling "
                        "down (retry next cycle)"
                    )
                else:
                    logger.exception("Unexpected error in account sync loop")
            interval = (
                self.check_interval_seconds
                if self._last_seen_authenticated or authed_before
                else self._UNAUTH_RETRY_INTERVAL_SECONDS
            )
            await asyncio.sleep(interval)

    def _filter_new_history(
        self,
        items: list[dict[str, Any]],
        *,
        last_view_at: int,
        last_bvid: str,
        seen_bvids_at_last_view_at: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], int, str]:
        newest_view_at = last_view_at
        newest_bvid = last_bvid
        seen_at_cursor = set(seen_bvids_at_last_view_at or set())
        if last_bvid:
            seen_at_cursor.add(last_bvid)
        accepted: list[dict[str, Any]] = []
        for item in items:
            history_meta = item.get("history", {})
            if not isinstance(history_meta, dict):
                history_meta = {}
            view_at = self._to_int(history_meta.get("view_at", item.get("view_at", 0)))
            bvid = str(history_meta.get("bvid", "")).strip()
            if view_at < last_view_at:
                continue
            if view_at == last_view_at and bvid and bvid in seen_at_cursor:
                continue
            accepted.append(item)
            if view_at > newest_view_at:
                newest_view_at = view_at
                newest_bvid = bvid
            elif view_at == newest_view_at and bvid:
                newest_bvid = bvid
        return accepted, newest_view_at, newest_bvid

    def _history_events(
        self,
        items: list[dict[str, Any]],
        *,
        snapshot: bool = False,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for item in items:
            history_meta = item.get("history", {})
            if not isinstance(history_meta, dict):
                history_meta = {}
            bvid = str(history_meta.get("bvid", "")).strip()
            view_at = self._to_int(history_meta.get("view_at", item.get("view_at", 0)))
            metadata: dict[str, Any] = {
                "bvid": bvid,
                "author": str(item.get("author", "")).strip(),
                "view_at": view_at,
                "source": "account_sync",
                "signal_strength": 0.35,
            }
            # ``view_at`` is the real watch moment. Mirroring it into the
            # canonical ``timestamp`` key is what lets the cognition lane
            # honour it — the ledger's ``created_at`` is the import moment
            # (a whole page shares one second), not when the video was seen.
            if view_at > 0:
                metadata["timestamp"] = view_at
            if snapshot:
                metadata["snapshot"] = True
            events.append(
                {
                    "event_type": "view",
                    "title": str(item.get("title", "")).strip(),
                    "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                    "metadata": metadata,
                }
            )
        return events

    def _history_cursor_bvids(
        self,
        items: list[dict[str, Any]],
        view_at: int,
        *,
        fallback_bvid: str = "",
        previous_seen: set[str] | None = None,
    ) -> list[str]:
        bvids: set[str] = set()
        if view_at > 0:
            for item in items:
                history_meta = item.get("history", {})
                if not isinstance(history_meta, dict):
                    history_meta = {}
                item_view_at = self._to_int(history_meta.get("view_at", item.get("view_at", 0)))
                if item_view_at != view_at:
                    continue
                bvid = str(history_meta.get("bvid", "")).strip()
                if bvid:
                    bvids.add(bvid)
        if previous_seen:
            bvids.update(str(item).strip() for item in previous_seen if str(item).strip())
        if fallback_bvid:
            bvids.add(fallback_bvid)
        return sorted(bvids)

    def _favorite_signature(self, folders: list[Any]) -> str:
        parts: list[str] = []
        for folder in folders:
            folder_id = str(getattr(getattr(folder, "folder", None), "media_id", ""))
            item_ids = sorted(
                str(item.get("bvid", "")).strip()
                for item in getattr(folder, "items", [])
                if isinstance(item, dict) and str(item.get("bvid", "")).strip()
            )
            if folder_id and item_ids:
                parts.append(f"{folder_id}:{','.join(item_ids)}")
        return "|".join(sorted(parts))

    def _favorite_bvids(self, folders: list[Any]) -> list[str]:
        bvids = {
            str(item.get("bvid", "")).strip()
            for folder in folders
            for item in getattr(folder, "items", [])
            if isinstance(item, dict) and str(item.get("bvid", "")).strip()
        }
        return sorted(bvids)

    def _mark_favorites_seen(self, bvids: list[str]) -> None:
        """Best-effort: record the favourites snapshot in the seen ledger."""
        database = self.database
        if database is None or not bvids:
            return
        mark = getattr(database, "mark_items_seen", None)
        if not callable(mark):
            return
        try:
            added = int(mark("bilibili", bvids) or 0)
        except Exception:
            logger.debug("marking favourites as seen failed", exc_info=True)
            return
        if added:
            logger.info(
                "account sync: %d favourited video(s) joined the seen ledger",
                added,
            )

    def _favorite_bvids_from_state(self, state: dict[str, object]) -> set[str]:
        stored = self._string_set(state.get("favorite_bvids", []))
        if stored:
            return stored
        return self._bvids_from_signature(str(state.get("favorite_signature", "")))

    def _filter_favorite_folders(self, folders: list[Any], seen_bvids: set[str]) -> list[Any]:
        if not seen_bvids:
            return folders
        filtered: list[Any] = []
        for folder in folders:
            items = [
                item
                for item in getattr(folder, "items", [])
                if isinstance(item, dict)
                and str(item.get("bvid", "")).strip()
                and str(item.get("bvid", "")).strip() not in seen_bvids
            ]
            if not items:
                continue
            filtered.append(
                SimpleNamespace(
                    folder=getattr(folder, "folder", None),
                    items=items,
                    truncated=bool(getattr(folder, "truncated", False)),
                )
            )
        return filtered

    def _favorite_events(
        self,
        folders: list[Any],
        *,
        snapshot: bool = False,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for folder in folders:
            folder_obj = getattr(folder, "folder", None)
            folder_title = str(getattr(folder_obj, "title", "")).strip()
            folder_id = int(getattr(folder_obj, "media_id", 0) or 0)
            for item in getattr(folder, "items", []):
                if not isinstance(item, dict):
                    continue
                if favorite_item_is_dead(item):
                    # Taken-down videos stay in the folder with the literal
                    # title "已失效视频". Their content is gone, so they carry
                    # no preference signal — only noise the analyzer would read
                    # as an interest in "已失效视频".
                    continue
                bvid = str(item.get("bvid", "")).strip()
                upper = item.get("upper", {})
                if not isinstance(upper, dict):
                    upper = {}
                metadata: dict[str, Any] = {
                    "bvid": bvid,
                    "folder_id": folder_id,
                    "folder_title": folder_title,
                    "up_name": str(upper.get("name", "")).strip(),
                    "source": "account_sync",
                    "signal_strength": 1.0,
                }
                raw_cnt = item.get("cnt_info")
                cnt_info: dict[str, Any] = raw_cnt if isinstance(raw_cnt, dict) else {}
                for value, key in (
                    (item.get("fav_time"), "fav_time"),
                    (item.get("pubtime"), "pubtime"),
                    (cnt_info.get("play"), "play_count"),
                    (item.get("duration"), "video_duration_seconds"),
                ):
                    if isinstance(value, (int, float)) and value > 0:
                        metadata[key] = int(value)
                # ``fav_time`` is when the user saved this item; mirror it into
                # the canonical time key so a 500-item first import is not read
                # as 500 saves inside one second.
                if metadata.get("fav_time"):
                    metadata["timestamp"] = metadata["fav_time"]
                if snapshot:
                    metadata["snapshot"] = True
                events.append(
                    {
                        "event_type": "favorite",
                        "title": str(item.get("title", "")).strip(),
                        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                        "metadata": metadata,
                    }
                )
        return events

    async def _collect_following(self) -> tuple[list[Any], Exception | None]:
        """Fetch following across pages (cli.py:5477-5494 pattern).

        Stops on a short page or once ``following_max_pages`` is reached. On a
        page fetch raising, returns the users collected so far plus the
        exception so the caller can ingest the partial import *and* record the
        error (auth-expired precedence preserved upstream).
        """
        collected: list[Any] = []
        page = 1
        while page <= self.following_max_pages:
            try:
                page_users = await self.bilibili_client.get_following(
                    page=page,
                    page_size=self.following_page_size,
                )
            except Exception as exc:
                return collected, exc
            if not page_users:
                break
            collected.extend(page_users)
            if len(page_users) < self.following_page_size:
                break
            page += 1
        return collected, None

    def _following_signature(self, following: list[Any]) -> str:
        return ",".join(self._following_mids(following))

    def _following_mids(self, following: list[Any]) -> list[str]:
        mids = {
            str(getattr(user, "mid", "")).strip()
            for user in following
            if str(getattr(user, "mid", "")).strip()
        }
        return sorted(mids)

    def _following_mids_from_state(self, state: dict[str, object]) -> set[str]:
        stored = self._string_set(state.get("following_mids", []))
        if stored:
            return stored
        return {
            item.strip()
            for item in str(state.get("following_signature", "")).split(",")
            if item.strip()
        }

    def _filter_following(self, following: list[Any], seen_mids: set[str]) -> list[Any]:
        if not seen_mids:
            return following
        return [
            user
            for user in following
            if str(getattr(user, "mid", "")).strip()
            and str(getattr(user, "mid", "")).strip() not in seen_mids
        ]

    def _following_events(
        self,
        following: list[Any],
        *,
        snapshot: bool = False,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for user in following:
            mid = int(getattr(user, "mid", 0) or 0)
            uname = str(getattr(user, "uname", "")).strip()
            metadata: dict[str, Any] = {
                "up_mid": mid,
                "up_name": uname,
                "sign": str(getattr(user, "sign", "")).strip(),
                "source": "account_sync",
                "signal_strength": 0.6,
            }
            # Bilibili returns the follow moment as ``mtime`` (epoch seconds).
            # It is the only field that distinguishes "I followed this creator
            # in 2019" from "this row was written during today's import"; the
            # ledger's ``created_at`` cannot, because a first sync commits the
            # whole page in the same second.
            mtime = self._to_int(getattr(user, "mtime", 0))
            if mtime > 0:
                metadata["mtime"] = mtime
                metadata["timestamp"] = mtime
            if snapshot:
                metadata["snapshot"] = True
            events.append(
                {
                    "event_type": "follow",
                    "title": uname,
                    "url": f"https://space.bilibili.com/{mid}" if mid else "",
                    "metadata": metadata,
                }
            )
        return events

    def _is_due(self, last_sync_at: str) -> bool:
        parsed = self._parse_iso_datetime(last_sync_at)
        if parsed is None:
            return True
        return self._now() - parsed >= timedelta(hours=self.sync_interval_hours)

    def _parse_iso_datetime(self, value: str) -> datetime | None:
        if not value:
            return None
        with_timezone = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(with_timezone)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _now(self) -> datetime:
        return datetime.now(tz=UTC)

    @staticmethod
    def _to_int(value: object) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _string_set(value: object) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {str(item).strip() for item in value if str(item).strip()}

    @staticmethod
    def _bvids_from_signature(signature: str) -> set[str]:
        bvids: set[str] = set()
        for folder_part in signature.split("|"):
            _, sep, item_part = folder_part.partition(":")
            if not sep:
                continue
            bvids.update(item.strip() for item in item_part.split(",") if item.strip())
        return bvids
