"""Continuous refresh controller for the local API runtime."""

from __future__ import annotations

import asyncio
import inspect
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

from openbiliclaw.config import SchedulerConfig
from openbiliclaw.discovery.pool_snapshot import build_pool_distribution_snapshot
from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD
from openbiliclaw.runtime.image_cache import (
    cleanup_image_cache,
    prefetch_cover,
    select_prefetch_targets,
)
from openbiliclaw.runtime.presence import PresenceTracker, background_llm_work_allowed
from openbiliclaw.soul.avoidance_speculator import choose_next_avoidance_candidate
from openbiliclaw.soul.speculator import (
    _normalize_probe_mode,
    build_probe_axis,
    choose_next_probe_candidate,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry

logger = logging.getLogger(__name__)

_MAX_DISCOVERY_BACKFILL_PER_REFRESH = 60
# How often the cover-image disk cache is pruned of consumed + unsaved covers.
# The bulk one-shot prune runs at API startup; this is the steady-state sweep.
_IMAGE_CACHE_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
# Discovery-time cover prefetch: cache covers while their CDN token is still fresh
# (XHS signed URLs expire fast). Runs often, scans recent discoveries newest-first,
# and is bounded per tick so it never floods a CDN.
_COVER_PREFETCH_INTERVAL_SECONDS = 60
_COVER_PREFETCH_RECENT_HOURS = 12
_COVER_PREFETCH_SCAN = 300
_COVER_PREFETCH_MAX_FETCH = 40
_DEFAULT_PLATFORM_SOURCE_SHARES: dict[str, int] = {
    "bilibili": 8,
}
_PLATFORM_SOURCE_ORDER = ("bilibili", "xiaohongshu", "douyin", "youtube")
_BILIBILI_DISCOVERY_SOURCES = ("search", "related_chain", "trending", "explore")
_PROBE_CHALLENGE_MODES = {"lateral", "bridge", "wildcard"}


def _call_accepts_limit(fn: Any) -> bool:
    """Return whether a producer callable accepts a ``limit=`` keyword."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "limit" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _call_accepts_strategy_limits(fn: Any) -> bool:
    """Return whether a discovery callable accepts ``strategy_limits=``."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "strategy_limits" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _call_accepts_pool_snapshot(fn: Any) -> bool:
    """Return whether a discovery callable accepts ``pool_snapshot=``."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "pool_snapshot" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


class SupportsRuntimeState(Protocol):
    def load_discovery_runtime_state(self) -> dict[str, object]: ...
    def save_discovery_runtime_state(self, state: dict[str, object]) -> None: ...
    def get_layer(self, name: str) -> Any: ...


class SupportsEventDatabase(Protocol):
    def query_events_since(
        self,
        *,
        after_event_id: int,
        event_types: list[str],
    ) -> list[dict[str, Any]]: ...
    def get_latest_event_id(self) -> int: ...
    def count_recommendations(self) -> int: ...
    def count_unread_recommendations(self) -> int: ...
    def count_pool_candidates(self, *, xhs_self_nickname: str = "") -> int: ...
    def count_pool_readiness(self, *, xhs_self_nickname: str = "") -> dict[str, int]: ...
    def count_pool_candidates_by_source(self) -> dict[str, int]: ...
    def count_pool_available_candidates_by_source(
        self, *, max_per_topic_group: int = 3, xhs_self_nickname: str = ""
    ) -> dict[str, int]: ...
    def count_pool_raw_material_candidates(self) -> int: ...
    def count_pool_raw_material_by_source(self) -> dict[str, int]: ...
    def get_pool_distribution_counts(self) -> dict[str, dict[str, int]]: ...
    def trim_explore_cluster_overflow(self, *, max_per_cluster: int = 3) -> int: ...
    def trim_topic_group_overflow(self, *, max_per_group: int) -> int: ...
    def trim_pool_to_target_count(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int] | None = None,
    ) -> int: ...
    def trim_pool_source_overflow(self, *, source_share_quotas: dict[str, int]) -> int: ...
    def reactivate_under_quota_pool_sources(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int],
        raw_source_share_quotas: dict[str, int] | None = None,
    ) -> int: ...
    def evict_stale_pool_items(self, *, max_age_days: int = 14) -> int: ...
    def iter_cover_lifecycle(self) -> list[tuple[str, str, bool]]: ...
    def iter_servable_cover_urls(
        self, *, recent_hours: int = 12, limit: int = 300
    ) -> list[str]: ...
    def get_notification_candidate(
        self,
        *,
        min_confidence: float = 0.82,
    ) -> dict[str, Any] | None: ...
    def mark_notification_sent(self, bvid: str) -> None: ...
    def get_delight_candidate(
        self,
        *,
        min_delight_score: float = DEFAULT_DELIGHT_THRESHOLD,
    ) -> dict[str, Any] | None: ...
    def get_delight_candidates(
        self,
        *,
        min_delight_score: float = DEFAULT_DELIGHT_THRESHOLD,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...
    def mark_delight_notified(self, bvid: str) -> None: ...
    def count_delight_candidates(
        self,
        *,
        min_delight_score: float = DEFAULT_DELIGHT_THRESHOLD,
    ) -> int: ...


class SupportsProfileEngine(Protocol):
    async def get_profile(self) -> Any: ...

    # Effective disliked topics (AI dislikes + flat preference dislikes with
    # user overrides applied). Used by the proactive-delight hard filter so a
    # manually added dislike filters and a manually removed one does not.
    def get_effective_disliked_topics(self) -> list[str]: ...

    # Optional: the soul engine exposes a ProfileUpdatePipeline that the
    # refresh loop ticks periodically. The attribute may be missing on
    # older test doubles, so callers should `getattr(..., "pipeline", None)`.
    @property
    def pipeline(self) -> Any: ...


class SupportsDiscoveryEngine(Protocol):
    async def discover(
        self,
        profile: Any,
        strategies: list[str] | None = None,
        limit: int = 30,
        *,
        strategy_limits: dict[str, int] | None = None,
        pool_snapshot: Any | None = None,
        fully_parallel: bool = False,
    ) -> list[Any]: ...


class SupportsRecommendationEngine(Protocol):
    async def generate_recommendations(
        self,
        discovered: list[Any] | None,
        profile: Any,
        limit: int = 10,
    ) -> list[Any]: ...

    async def precompute_pool_copy(
        self,
        *,
        profile: Any,
        limit: int,
    ) -> int: ...

    async def prewarm_supergroup_embeddings(self) -> int: ...

    async def prewarm_pool_mmr_embeddings(self, *, limit: int = 200) -> int: ...


# Staged strategy plan for guided-init pool backfill (gui-init spec §5d).
# Mirrors cli._INIT_DISCOVERY_PLAN; B2 consolidates the CLI to reuse this.
_INIT_DISCOVERY_PLAN: list[list[str]] = [
    ["search", "trending", "related_chain", "explore"],
]


@dataclass
class ContinuousRefreshController:
    """Keep discovery cache and recommendations fresh during API runtime."""

    memory_manager: SupportsRuntimeState
    database: SupportsEventDatabase
    soul_engine: SupportsProfileEngine
    discovery_engine: SupportsDiscoveryEngine
    recommendation_engine: SupportsRecommendationEngine
    event_hub: Any | None = None
    discovery_candidate_pipeline: Any | None = None
    xhs_producer: Any | None = None
    douyin_producer: Any | None = None
    youtube_producer: Any | None = None
    scheduler_config: Any = field(default_factory=SchedulerConfig)
    presence: PresenceTracker = field(default_factory=PresenceTracker)
    # gui-init D1: optional init-aware gate. When it returns True (a guided init
    # is active) ALL background loops pause so they don't race init's explicit
    # analyze/build. ``run_init_backfill`` bypasses this (it never calls
    # ``_llm_work_allowed``), so init's own discovery is not self-blocked.
    init_active_check: Callable[[], bool] | None = None
    signal_event_threshold: int = 6
    event_refresh_minutes: int = 0
    trending_refresh_hours: int = 3
    explore_refresh_hours: int = 12
    notification_cooldown_hours: int = 2
    delight_cooldown_hours: int = 4
    check_interval_seconds: int = 60
    # Proactive probe-push loop runs much less frequently than the main
    # refresh loop.  Probes aren't streaming content — once the active
    # set has been delivered, the only reason to push again is when a
    # slot rotates (user feedback / TTL).  10 min is enough to surface
    # newly generated probes without hammering the user.
    # Pre-2026-05-04 default was 600s (10 min). At that cadence new
    # delights took up to 10 minutes to surface in the popup, plus the
    # proactive_push only emits ONE candidate per tick. 120s is a much
    # tighter fallback while keeping chrome-notification cooldowns
    # intact (those have their own dedup window). The primary push path
    # is still the immediate ``delight.refreshed`` event emitted at the
    # end of ``_run_refresh_plan`` once new candidates are scored — this
    # interval is a safety net for the case where a refresh-less window
    # produces delights via some other path (manual rescore, init).
    proactive_push_interval_seconds: int = 120
    # Soul pipeline tick runs every minute to drain buffers, but the
    # speculator inside the pipeline doesn't need that cadence — its
    # gating happens upstream now in pipeline.tick().  Kept explicit so
    # we can tune in tests.
    discovery_limit: int = 30
    pool_target_count: int = 300
    pool_source_shares: dict[str, int] = field(
        default_factory=lambda: dict(_DEFAULT_PLATFORM_SOURCE_SHARES)
    )
    # v0.3.63+: optional registry so detached tasks (manual-refresh
    # background work, per-strategy precompute fire-and-forget) can be
    # cancelled by ``RuntimeContext.rebuild_from_config`` before the
    # next runtime starts. ``_track_task`` uses bare ``create_task``
    # when this is ``None`` so existing tests that build the controller
    # directly without injecting a registry keep working.
    task_registry: BackgroundTaskRegistry | None = None
    _manual_refresh_task: asyncio.Task[None] | None = None
    _discovery_drain_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )
    # v0.3.62+ global "skip-if-busy" gate. Four entry points
    # (_loop_refresh, _complete_manual_refresh, refresh_after_event_ingest,
    # refresh_after_feedback) all funnel through ``refresh_if_needed``.
    # Without this lock, a slow periodic tick (10+ minutes when WBI
    # rate-limits) can run concurrently with manual refresh + per-event
    # opportunistic refresh, amplifying load on Bilibili and causing
    # SQLite write contention. Acquired with ``async with`` inside
    # ``refresh_if_needed``; if already held, the new caller exits
    # immediately with ``{"skipped": True, ...}`` rather than queueing.
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _manual_refresh_state: str = "idle"
    _manual_refresh_message: str = ""
    _manual_refresh_started_at: str = ""
    _manual_refresh_finished_at: str = ""
    # Last-tick fingerprint of pool maintenance state, used to demote
    # the per-minute "reactivated=N" / "trim dropped=N top=X" log lines
    # to DEBUG when nothing actually changed since the previous tick.
    # INFO fires only when the count or top-group rotates.
    _last_pool_maintenance_fingerprint: tuple[int, int, str] = (-1, -1, "")
    _warned_pool_count_fallbacks: set[str] = field(default_factory=set, init=False)
    # Last pool_available count emitted via the runtime event stream so
    # popup-side ``mergeRuntimeStatusEvent`` only re-renders when the
    # number actually changes — see ``_publish_pool_status_if_changed``.
    _last_published_pool_count: int = -1
    # Flips false→true when soul profile is first detected. Used by
    # ``_loop_refresh`` to fire a one-shot ``classify_pool_backlog``
    # the moment init's analyze_events finishes — otherwise items
    # ingested during the ~7-minute init window sit un-classified
    # until the next natural refresh tick (and recommendation summary
    # would print fallback ``topic_group="title[:N]"`` until then).
    _profile_ready_observed: bool = False
    # v0.3.61+: skip the first ``refresh_if_needed`` invocation after
    # daemon start to give Bilibili a 30s cool-down window. Init's
    # synchronous chunk (history fetch + favorites + following) hits
    # the WBI search backend hard in the first ~10s; firing discovery
    # search queries immediately afterwards routinely triggers
    # v_voucher storm. One refresh tick of grace = much fewer
    # exhausted retries on the first half-hour.
    _init_grace_consumed: bool = False
    _last_llm_gate_allowed: bool = field(default=True, init=False)

    _signal_event_types = [
        "view",
        "search",
        "favorite",
        "like",
        "coin",
        "comment",
        "feedback",
    ]

    def _llm_work_allowed(self) -> bool:
        """Return whether daemon-owned background LLM / embedding work can run."""
        # Pause every background loop while a guided init is active (gui-init
        # D1) — the continuous refresh / soul-pipeline / producer ticks all gate
        # on this, so init's explicit analyze/build/backfill runs uncontended.
        if self.init_active_check is not None:
            try:
                if self.init_active_check():
                    return False
            except Exception:
                pass
        allowed = background_llm_work_allowed(self.scheduler_config, self.presence)
        if allowed != self._last_llm_gate_allowed:
            logger.info(
                "Background LLM work gate %s",
                "allowed" if allowed else "blocked",
            )
            self._last_llm_gate_allowed = allowed
        return allowed

    def _xhs_self_nickname(self) -> str:
        """Return the persisted XHS self nickname for pool guards."""
        try:
            state = self.memory_manager.load_discovery_runtime_state()
        except Exception:
            return ""
        info = state.get("xhs_self_info")
        if not isinstance(info, dict):
            return ""
        return str(info.get("nickname", "") or "").strip()

    def _pool_readiness_counts(self) -> dict[str, int]:
        """Return normalized pool readiness counts for status payloads."""
        nickname = self._xhs_self_nickname()
        try:
            readiness = self.database.count_pool_readiness(xhs_self_nickname=nickname)
            available = int(readiness.get("available", 0))
            return {
                "available": max(0, available),
                "raw": max(0, int(readiness.get("raw", available))),
                "pending": max(0, int(readiness.get("pending", 0))),
                "pending_eval": max(0, int(readiness.get("pending_eval", 0))),
                "evaluated_pending": max(0, int(readiness.get("evaluated_pending", 0))),
            }
        except Exception:
            available = int(self.database.count_pool_candidates(xhs_self_nickname=nickname))
            return {
                "available": max(0, available),
                "raw": max(0, available),
                "pending": 0,
                "pending_eval": 0,
                "evaluated_pending": 0,
            }

    @staticmethod
    def _pool_count_payload(counts: dict[str, int]) -> dict[str, int]:
        return {
            "pool_available_count": int(counts.get("available", 0)),
            "pool_raw_count": int(counts.get("raw", counts.get("available", 0))),
            "pool_pending_count": int(counts.get("pending", 0)),
            "pool_pending_eval_count": int(counts.get("pending_eval", 0)),
            "pool_evaluated_pending_count": int(counts.get("evaluated_pending", 0)),
        }

    def get_runtime_status(self) -> dict[str, object]:
        """Build a lightweight runtime summary for popup or diagnostics."""
        state = self.memory_manager.load_discovery_runtime_state()
        refresh_values = [
            str(state.get("last_event_refresh_at", "")),
            str(state.get("last_trending_refresh_at", "")),
            str(state.get("last_explore_refresh_at", "")),
        ]
        parsed_refresh_values: list[datetime] = []
        for value in refresh_values:
            parsed = self._parse_iso_datetime(value)
            if parsed is not None:
                parsed_refresh_values.append(parsed)
        last_refresh_at = max(parsed_refresh_values).isoformat() if parsed_refresh_values else ""
        pending_delight_count = 0
        with suppress(Exception):
            pending_delight_count = self.database.count_delight_candidates(
                min_delight_score=DEFAULT_DELIGHT_THRESHOLD,
            )
        pool_counts = self._pool_readiness_counts()
        return {
            "initialized": self._is_initialized(),
            "recommendation_count": self.database.count_recommendations(),
            "pending_signal_events": self._pending_signal_events_count(state),
            "last_refresh_at": last_refresh_at,
            "last_notification_at": str(state.get("last_notification_at", "")),
            "unread_count": self.database.count_unread_recommendations(),
            **self._pool_count_payload(pool_counts),
            "pool_target_count": self.pool_target_count,
            "last_discovered_count": self._int_state_value(state, "last_discovered_count"),
            "last_replenished_count": self._int_state_value(state, "last_replenished_count"),
            "recent_pool_topics": self._list_state_value(state, "recent_pool_topics"),
            "manual_refresh_state": self._manual_refresh_state,
            "manual_refresh_message": self._manual_refresh_message,
            "pending_delight_count": pending_delight_count,
            "last_delight_notification_at": str(state.get("last_delight_notification_at", "")),
        }

    async def refresh_if_needed(self) -> dict[str, object]:
        """Refresh discovery candidates when thresholds are met.

        v0.3.62+ semantics — this is the single global gate for all
        four refresh entry points (``_loop_refresh``,
        ``_complete_manual_refresh``, ``refresh_after_event_ingest``,
        ``refresh_after_feedback``). A module-level
        ``_refresh_lock`` (an ``asyncio.Lock``) is checked at the very
        top: if another refresh is already in progress, this call
        returns ``{"skipped": True, "reason": "another refresh holds
        lock"}`` immediately rather than queueing. The "skip if locked"
        rather than "wait in queue" pattern is deliberate — manual
        refresh requests should not stack up behind a slow periodic
        tick (which can take 10+ minutes when Bilibili WBI rate-limits
        every request). The remaining body runs inside ``async with
        self._refresh_lock:``, so the lock is released even on
        exception paths.

        Internal helpers (``_run_refresh_plan``, ``force_refresh``)
        intentionally do NOT acquire this lock — only the public
        ``refresh_if_needed`` entry does, so callers reaching it from
        different paths can't double-acquire.
        """
        if not self._llm_work_allowed():
            return {"refreshed": False, "strategies": [], "reason": "llm_paused"}

        if self._refresh_lock.locked():
            logger.debug("refresh_if_needed skipped: another refresh in flight")
            return {"skipped": True, "reason": "another refresh holds lock"}

        async with self._refresh_lock:
            state = self.memory_manager.load_discovery_runtime_state()
            if not self._is_initialized():
                return {"refreshed": False, "strategies": [], "reason": "not_initialized"}

            pool_at_cap = self._enforce_pool_cap()
            await self._publish_pool_status_if_changed()
            if pool_at_cap:
                return {"refreshed": False, "strategies": [], "reason": "pool_at_cap"}

            profile = await self.soul_engine.get_profile()
            plan = self._build_refresh_plan(state)
            if not plan:
                return {"refreshed": False, "strategies": [], "reason": "below_threshold"}

            return await self._run_refresh_plan(
                state=state,
                profile=profile,
                plan=plan,
                reason="triggered",
            )

    async def run_init_backfill(
        self,
        profile: Any,
        target_pool_count: int,
        *,
        fully_parallel: bool = True,
    ) -> int:
        """Backfill the initial discovery pool for guided init.

        Holds ``_refresh_lock`` so it serializes with continuous refresh and
        never races it on ``content_cache`` (gui-init spec §5d). Mirrors the
        CLI's staged ``_INIT_DISCOVERY_PLAN`` backfill, but against this
        controller's live ``discovery_engine``/``database``. Cooperative
        cancel: ``async with`` releases the lock on ``CancelledError``.
        Returns the total number of items discovered.
        """
        discovered_count = 0
        async with self._refresh_lock:
            for strategies in _INIT_DISCOVERY_PLAN:
                current = self.database.count_pool_candidates()
                if current >= target_pool_count:
                    break
                request_limit = max(20, target_pool_count - current)
                discovered = await self.discovery_engine.discover(
                    profile,
                    strategies=strategies,
                    limit=request_limit,
                    fully_parallel=fully_parallel,
                )
                discovered_count += len(discovered)
        return discovered_count

    async def force_refresh(self) -> dict[str, object]:
        """Run a full refresh immediately, bypassing runtime thresholds.

        Runs all 4 Bilibili strategies in a single discover() call so they
        execute concurrently via asyncio.gather, maximizing pool diversity. The pool
        target still applies as a hard cap — if the pool is already full, no
        discovery runs and overflow is trimmed.

        v0.3.62+: also acquires ``_refresh_lock`` so manual refresh
        (which calls ``force_refresh`` rather than ``refresh_if_needed``)
        respects the global skip-if-busy gate. Without this, periodic
        + event-ingest refresh could run concurrently with a manual
        refresh, amplifying Bilibili API load and SQLite write contention.
        Skip semantics match ``refresh_if_needed``: return immediately
        with ``{"refreshed": False, "reason": "another refresh holds lock"}``
        instead of queueing.
        """
        if self._refresh_lock.locked():
            logger.debug("force_refresh skipped: another refresh in flight")
            return {
                "refreshed": False,
                "strategies": [],
                "reason": "another refresh holds lock",
            }
        async with self._refresh_lock:
            return await self._force_refresh_locked()

    async def _force_refresh_locked(self) -> dict[str, object]:
        state = self.memory_manager.load_discovery_runtime_state()
        if not self._is_initialized():
            return {"refreshed": False, "strategies": [], "reason": "not_initialized"}

        pool_at_cap = self._enforce_pool_cap()
        await self._publish_pool_status_if_changed()
        if pool_at_cap:
            return {"refreshed": False, "strategies": [], "reason": "pool_at_cap"}

        profile = await self.soul_engine.get_profile()
        plan = self._build_source_replenishment_plan()
        if not plan:
            return {"refreshed": False, "strategies": [], "reason": "below_threshold"}
        return await self._run_refresh_plan(
            state=state,
            profile=profile,
            plan=plan,
            reason="manual",
        )

    def _enforce_pool_cap(self) -> bool:
        """Run pool maintenance and report whether frontend availability is at target.

        ``pool_target_count`` is a frontend-visible availability floor, not the
        raw material cap. Raw rows may exceed it until ``_raw_material_ceiling``.
        """
        source_targets = self._source_target_counts()
        raw_source_targets = self._raw_source_target_counts()

        # Cross-source topic_group quota runs every tick, not just inside
        # _run_refresh_plan: when pool sits at cap, refresh exits before
        # discover, so the in-plan trim would never fire and pre-existing
        # topic concentration would persist indefinitely. This call is a
        # cheap SQL group-by + UPDATE, safe to run unconditionally.
        try:
            self.database.trim_topic_group_overflow(
                max_per_group=max(3, self.pool_target_count // 10),
            )
        except Exception:
            logger.exception("trim_topic_group_overflow failed")

        reactivate_fn = getattr(self.database, "reactivate_under_quota_pool_sources", None)
        if callable(reactivate_fn):
            try:
                reactivated = reactivate_fn(
                    target=self.pool_target_count,
                    source_share_quotas=source_targets,
                    raw_source_share_quotas=raw_source_targets,
                )
                if reactivated > 0:
                    # Demote to DEBUG when the count is identical to the
                    # previous tick — pool sitting in steady-state with
                    # the same N items reactivating each minute is noise,
                    # not signal. INFO fires only when N changes (real
                    # state transition: pool drained to refill, or new
                    # source surge).
                    last_reactivated = self._last_pool_maintenance_fingerprint[1]
                    log_fn = logger.info if reactivated != last_reactivated else logger.debug
                    log_fn(
                        "enforce_pool_cap: reactivated=%s under-quota source items",
                        reactivated,
                    )
                    self._last_pool_maintenance_fingerprint = (
                        self._last_pool_maintenance_fingerprint[0],
                        reactivated,
                        self._last_pool_maintenance_fingerprint[2],
                    )
                    self.database.trim_topic_group_overflow(
                        max_per_group=max(3, self.pool_target_count // 10),
                    )
            except Exception:
                logger.exception("reactivate_under_quota_pool_sources failed")

        trim_source_overflow_fn = getattr(self.database, "trim_pool_source_overflow", None)
        if callable(trim_source_overflow_fn):
            try:
                source_overflow_suppressed = trim_source_overflow_fn(
                    source_share_quotas=raw_source_targets,
                )
                if source_overflow_suppressed > 0:
                    logger.info(
                        "enforce_pool_cap: suppressed=%s over-quota source items",
                        source_overflow_suppressed,
                    )
                    self.database.trim_topic_group_overflow(
                        max_per_group=max(3, self.pool_target_count // 10),
                    )
            except Exception:
                logger.exception("trim_pool_source_overflow failed")

        pool_available = self.database.count_pool_candidates(
            xhs_self_nickname=self._xhs_self_nickname()
        )
        raw_ceiling = self._raw_material_ceiling()
        trimmed = 0
        try:
            trimmed = self.database.trim_pool_to_target_count(
                target=raw_ceiling,
                source_share_quotas=raw_source_targets,
            )
        except Exception:
            logger.exception("trim_pool_to_target_count failed")
        if trimmed > 0:
            pool_available = self.database.count_pool_candidates(
                xhs_self_nickname=self._xhs_self_nickname()
            )
            logger.info(
                "enforce_pool_cap: raw_trimmed=%s, pool_available=%s, target=%s, raw_ceiling=%s",
                trimmed,
                pool_available,
                self.pool_target_count,
                raw_ceiling,
            )
        else:
            logger.debug(
                "enforce_pool_cap: no raw trim needed, "
                "pool_available=%s, target=%s, raw_ceiling=%s",
                pool_available,
                self.pool_target_count,
                raw_ceiling,
            )
        return pool_available >= self.pool_target_count

    async def trigger_manual_refresh(self) -> dict[str, object]:
        """Schedule one background manual refresh without blocking the caller."""
        if not self._is_initialized():
            return {"accepted": False, "state": "idle", "reason": "not_initialized"}
        if self._manual_refresh_task is not None and not self._manual_refresh_task.done():
            return {"accepted": True, "state": "running", "reason": "already_running"}

        self._manual_refresh_state = "running"
        self._manual_refresh_message = "正在补货…"
        self._manual_refresh_started_at = self._now().isoformat()
        self._manual_refresh_finished_at = ""
        self._manual_refresh_task = self._track_task(
            "manual_refresh",
            self._complete_manual_refresh(),
        )
        return {"accepted": True, "state": "running", "reason": "started"}

    def _track_task(
        self,
        name: str,
        coro: Any,
    ) -> asyncio.Task[Any]:
        """Spawn a detached task, routing through the registry when available.

        v0.3.63+: when ``self.task_registry`` is wired (by
        ``RuntimeContext`` at startup), the task is registered so that
        ``rebuild_from_config``'s ``cancel_all`` can cancel it before
        the new runtime starts. Tests that construct the controller
        directly (no registry) fall back to bare
        ``asyncio.create_task`` for backward compat.
        """
        registry = self.task_registry
        if registry is not None:
            return registry.track(name, coro)
        return asyncio.create_task(coro, name=name)

    def get_pending_notification(self) -> dict[str, object] | None:
        """Return one recommendation candidate for browser notification."""
        state = self.memory_manager.load_discovery_runtime_state()
        last_notification_at = self._parse_iso_datetime(str(state.get("last_notification_at", "")))
        if last_notification_at is not None and self._now() - last_notification_at < timedelta(
            hours=self.notification_cooldown_hours
        ):
            return None
        candidate = self.database.get_notification_candidate(min_confidence=0.82)
        if candidate is None:
            return None
        return {
            "recommendation_id": int(candidate["id"]),
            "bvid": str(candidate.get("bvid", "")),
            "title": str(candidate.get("title", "")),
            "reason": str(candidate.get("expression", "")),
        }

    def mark_notification_sent(self, bvid: str) -> None:
        """Persist notification delivery markers."""
        self.database.mark_notification_sent(bvid)
        state = self.memory_manager.load_discovery_runtime_state()
        state["last_notification_at"] = self._now().isoformat()
        self.memory_manager.save_discovery_runtime_state(state)

    def get_pending_delight(self) -> dict[str, object] | None:
        """Return one proactive delight candidate for browser notification.

        Honors the user's ``disliked_topics`` (from the preference layer)
        as a hard filter — a video whose title contains a disliked topic
        phrase is skipped even if its delight_score otherwise qualifies.
        """
        state = self.memory_manager.load_discovery_runtime_state()
        last_delight_at = self._parse_iso_datetime(
            str(state.get("last_delight_notification_at", ""))
        )
        if last_delight_at is not None and self._now() - last_delight_at < timedelta(
            hours=self.delight_cooldown_hours
        ):
            return None

        # Pull a small batch and filter disliked topics in Python — there
        # are typically only a handful of high-score candidates and a
        # very short disliked list, so the overhead is negligible.
        candidates = self.database.get_delight_candidates(
            min_delight_score=DEFAULT_DELIGHT_THRESHOLD,
            limit=20,
        )
        if not candidates:
            return None

        disliked_phrases = self._load_disliked_topic_phrases()
        candidate: dict[str, Any] | None = None
        for row in candidates:
            title = str(row.get("title", "")).lower()
            tags_raw = str(row.get("tags", "")).lower()
            haystack = f"{title} {tags_raw}"
            if any(phrase in haystack for phrase in disliked_phrases if phrase):
                continue
            candidate = row
            break
        if candidate is None:
            return None
        return {
            "bvid": str(candidate.get("bvid", "")),
            "title": str(candidate.get("title", "")),
            "delight_reason": str(candidate.get("delight_reason", "")),
            "delight_score": float(candidate.get("delight_score", 0.0) or 0.0),
            "delight_hook": str(candidate.get("delight_hook", "")),
            "cover_url": str(candidate.get("cover_url", "")),
            "content_url": str(candidate.get("content_url", "")),
            "source_platform": str(candidate.get("source_platform", "") or "bilibili"),
        }

    def _load_disliked_topic_phrases(self) -> list[str]:
        """Return lowercased *effective* disliked-topic substrings.

        Sourced from the soul engine's ``get_effective_disliked_topics`` —
        AI dislikes ∪ flat preference dislikes, with user overrides applied
        (base-then-overlay), so a manually added dislike filters here and a
        manually removed one does not. Phrases are case-insensitive substring
        matches against title + tags. Falls back to the raw preference layer
        for older soul-engine doubles lacking the method.
        """
        getter = getattr(self.soul_engine, "get_effective_disliked_topics", None)
        if callable(getter):
            try:
                return [str(item).strip().lower() for item in getter() if str(item).strip()]
            except Exception:
                return []
        try:
            layer = self.memory_manager.get_layer("preference")
        except Exception:
            return []
        data = getattr(layer, "data", None)
        if not isinstance(data, dict):
            return []
        raw = data.get("disliked_topics")
        if not isinstance(raw, list):
            return []
        return [str(item).strip().lower() for item in raw if str(item).strip()]

    def mark_delight_sent(self, bvid: str) -> None:
        """Persist delight notification delivery markers."""
        self.database.mark_delight_notified(bvid)
        state = self.memory_manager.load_discovery_runtime_state()
        state["last_delight_notification_at"] = self._now().isoformat()
        self.memory_manager.save_discovery_runtime_state(state)

    async def prepare_delight_candidates(self) -> int:
        """Warm ready-to-push delight candidates even when no refresh runs."""
        if not self._is_initialized():
            return 0
        profile = await self.soul_engine.get_profile()
        return await self.recommendation_engine.precompute_pool_copy(
            profile=profile,
            limit=0,
        )

    async def _safe_precompute_pool_copy(self, *, profile: Any) -> int:
        """Run ``precompute_pool_copy`` swallowing any exception.

        v0.3.47+ uses this from per-strategy fire-and-forget tasks in
        ``_run_refresh_plan``. The lock inside the engine queues
        concurrent calls so two strategies don't double-spend LLM
        tokens; this wrapper exists so a single failed expression
        batch doesn't take down the whole refresh round (caller does
        ``return_exceptions=True`` on the gather, but a logged warning
        from one place is cleaner than scattering try/except).
        """
        try:
            return await self.recommendation_engine.precompute_pool_copy(
                profile=profile,
                limit=_MAX_DISCOVERY_BACKFILL_PER_REFRESH,
            )
        except Exception:
            logger.exception("precompute_pool_copy task failed")
            return 0

    async def _safe_prewarm_pool_mmr_embeddings(self) -> int:
        """Warm MMR embeddings without blocking refresh completion."""
        try:
            return int(await self.recommendation_engine.prewarm_pool_mmr_embeddings())
        except Exception:
            logger.exception("prewarm_pool_mmr_embeddings failed")
            return 0

    async def _safe_prewarm_supergroup_embeddings(self) -> int:
        """Warm topic-supergroup embeddings without blocking refresh completion."""
        try:
            return int(await self.recommendation_engine.prewarm_supergroup_embeddings())
        except Exception:
            logger.exception("prewarm_supergroup_embeddings failed")
            return 0

    async def run_forever(self) -> None:
        """Launch all background tasks as independent concurrent loops.

        Each task runs on its own timer so a slow discovery refresh
        (10+ minutes when B站 API challenges every request) never
        blocks proactive notifications, soul pipeline ticks, or XHS
        keyword production.

        Architecture::

            ┌─ _loop_refresh()           60s   LLM-heavy, may take minutes
            ├─ _loop_pool_precompute()   60s   v0.3.60+ — drain pool_expression
            ├─ _loop_soul_pipeline()     60s   profile updates, speculator
            ├─ _loop_xhs_producer()      60s   xhs keyword generation
            ├─ _loop_douyin_producer()   60s   Douyin discovery when under quota
            ├─ _loop_youtube_producer()  60s   YouTube discovery when under quota
            ├─ _loop_proactive_push()    60s   delight + interest probe
            ├─ _loop_image_cache_cleanup() 6h  prune consumed+unsaved covers
            └─ _loop_cover_prefetch()    60s   cache fresh-token covers (XHS)
        """
        if self._llm_work_allowed():
            with suppress(Exception):
                await self.prepare_delight_candidates()
        self._warn_on_stranded_source_shares()
        tasks = [
            asyncio.create_task(self._loop_refresh()),
            asyncio.create_task(self._loop_pool_precompute()),
            asyncio.create_task(self._loop_soul_pipeline()),
            asyncio.create_task(self._loop_xhs_producer()),
            asyncio.create_task(self._loop_douyin_producer()),
            asyncio.create_task(self._loop_youtube_producer()),
            asyncio.create_task(self._loop_proactive_push()),
            asyncio.create_task(self._loop_image_cache_cleanup()),
            asyncio.create_task(self._loop_cover_prefetch()),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _loop_refresh(self) -> None:
        """Discovery refresh — fills the candidate pool."""
        while True:
            # v0.3.61+: 30s init grace period. The very first refresh
            # tick after daemon start lands while Bilibili's WBI
            # rate-limit bucket is still saturated from init's history
            # / favorites / following burst — firing discovery search
            # immediately produces ~50% v_voucher exhaustion. Skipping
            # the first refresh_if_needed gives the IP a single tick
            # to cool down before discovery starts hammering it.
            if not self._init_grace_consumed:
                self._init_grace_consumed = True
                logger.info(
                    "Init grace period — skipping first refresh tick to let "
                    "Bilibili WBI bucket cool down (next tick will run normally)"
                )
            elif not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            else:
                with suppress(Exception):
                    await self._on_profile_ready_if_first_time()
                with suppress(Exception):
                    await self.refresh_if_needed()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_pool_precompute(self) -> None:
        """v0.3.60+: drain pool_expression / pool_topic_label independently.

        v0.3.59 added ``_drain_pool_precompute_backlog`` to ``_loop_refresh``
        but placed it AFTER ``await self.refresh_if_needed()``. Production
        debugging on 2026-05-05 (PID 32644 daemon, started 22:35:12) found
        runtime stuck at ``manual_refresh_state="running"`` because B 站
        v_voucher rate limit kept refresh_if_needed pending for many
        minutes — the drain queued behind it never executed, even with
        184 fresh items in pool waiting for expression copy.

        Splitting the drain into its own loop matches the ``run_forever``
        contract every other ticker honours: a slow refresh must NEVER
        block independent maintenance work. Engine's ``_precompute_lock``
        still dedupes against per-strategy fire-and-forget tasks queued
        by ``_run_refresh_plan`` so no LLM token double-spend.
        """
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._drain_pool_precompute_backlog()
            await asyncio.sleep(self.check_interval_seconds)

    async def _drain_pool_precompute_backlog(self) -> None:
        """v0.3.59+: independent precompute drain.

        Fires ``precompute_pool_copy`` once per refresh-loop tick (60s)
        if the soul profile is ready. The engine's ``_precompute_lock``
        de-dupes against per-strategy fire-and-forget tasks queued by
        ``_run_refresh_plan`` so back-to-back triggers don't double-spend
        LLM tokens.
        """
        engine = self.recommendation_engine
        if engine is None:
            return
        if not self._is_initialized():
            return
        try:
            profile = await self.soul_engine.get_profile()
        except Exception:
            return
        if profile is None:
            return
        try:
            before_pool_count = int(
                self.database.count_pool_candidates(xhs_self_nickname=self._xhs_self_nickname())
            )
        except Exception:
            before_pool_count = -1
        try:
            await engine.precompute_pool_copy(
                profile=profile,
                limit=_MAX_DISCOVERY_BACKFILL_PER_REFRESH,
            )
        except Exception:
            logger.exception("Periodic precompute drain failed")
            return
        if before_pool_count >= 0:
            await self._publish_precompute_replenishment_if_needed(
                before_pool_count=before_pool_count,
            )

    async def _publish_precompute_replenishment_if_needed(
        self,
        *,
        before_pool_count: int,
    ) -> None:
        """Report candidates that became usable during the standalone drain."""
        try:
            after_pool_counts = self._pool_readiness_counts()
            after_pool_count = int(after_pool_counts["available"])
        except Exception:
            return
        replenished_count = max(0, after_pool_count - int(before_pool_count))
        if replenished_count <= 0:
            return

        state = self.memory_manager.load_discovery_runtime_state()
        state["last_replenished_count"] = replenished_count
        discovered_count = self._int_state_value(state, "last_discovered_count")
        recent_pool_topics = self._list_state_value(state, "recent_pool_topics")
        self.memory_manager.save_discovery_runtime_state(state)
        self._last_published_pool_count = after_pool_count
        logger.info(
            "Periodic precompute made %s pool candidates available (pool_available %s -> %s)",
            replenished_count,
            before_pool_count,
            after_pool_count,
        )
        await self._publish_event(
            {
                "type": "refresh.pool_updated",
                "phase": "done",
                "message": f"刚补进 {replenished_count} 条新的",
                **self._pool_count_payload(after_pool_counts),
                "last_discovered_count": discovered_count,
                "last_replenished_count": replenished_count,
                "recent_pool_topics": recent_pool_topics,
            }
        )

    async def _on_profile_ready_if_first_time(self) -> None:
        """One-shot hook fired the tick after soul profile first appears.

        Drains the un-classified pool backlog that piled up during init's
        analyze_events window. Without this, items entering the pool
        before profile-ready (XHS bootstrap notes, B站 history fetches)
        sit with empty ``topic_group`` / ``style_key`` until the next
        natural refresh tick — and the recommendation summary log shows
        fallback ``topic_group=title[:N]`` (the ugly "屎屎/165/三花"
        debug we saw on 2026-05-05).
        """
        if not self._llm_work_allowed():
            return
        if self._profile_ready_observed:
            return
        if not self._is_initialized():
            return
        self._profile_ready_observed = True
        engine = self.recommendation_engine
        classify_fn = getattr(engine, "classify_pool_backlog", None) if engine else None
        if not callable(classify_fn):
            return
        try:
            profile = await self.soul_engine.get_profile()
        except Exception:
            # Race: _is_initialized was true but get_profile raised.
            # Reset the flag so the next tick retries cleanly.
            self._profile_ready_observed = False
            return
        logger.info(
            "Soul profile became ready — kicking classify_pool_backlog to drain init-window backlog"
        )
        try:
            await classify_fn(profile=profile, limit=100)
        except Exception:
            logger.exception("profile-ready classify_pool_backlog failed")

    async def _loop_soul_pipeline(self) -> None:
        """Soul profile pipeline — buffer flushes, speculator, cognition."""
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_soul_pipeline()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_xhs_producer(self) -> None:
        """XHS keyword production — Soul-driven search task generation."""
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_xhs_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_douyin_producer(self) -> None:
        """Douyin production — plugin/direct discovery when Douyin is below quota."""
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_douyin_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_youtube_producer(self) -> None:
        """YouTube production — backend-direct discovery when YouTube is below quota."""
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_youtube_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_proactive_push(self) -> None:
        """Delight + interest probe push — lightweight, never blocks.

        Runs on a longer cadence than the main refresh loop because
        probes/delight are not streaming content — once the active set
        has been delivered, additional pushes within minutes only
        contribute notification fatigue.
        """
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.proactive_push_interval_seconds)
                continue
            # Score un-scored pool items even when the discovery refresh
            # tick early-exits (pool_at_cap or below_threshold). Without
            # this, a steady-state pool that sits at cap silently starves
            # delight scoring — observed 2026-05-04: scoring last ran on
            # daemon startup at 03:15 and stopped for 9.5 hours because
            # _run_refresh_plan never reached the precompute_pool_copy
            # branch. ``prepare_delight_candidates`` calls precompute_pool_copy
            # with limit=0, which still runs precompute_delight_scores on
            # the up-to-50 un-scored items (relevance >= 0.55).
            with suppress(Exception):
                await self.prepare_delight_candidates()
            # Snapshot delight count BEFORE prepare so we can detect a
            # net new above-threshold delight (popup re-fetch trigger).
            delight_count_before = self._safe_count_delight_candidates()
            with suppress(Exception):
                await self._publish_delight_if_available()
            with suppress(Exception):
                await self._publish_probe_if_available()
            delight_count_after = self._safe_count_delight_candidates()
            net_new_delights = max(0, delight_count_after - delight_count_before)
            if net_new_delights > 0:
                with suppress(Exception):
                    await self._publish_event(
                        {
                            "type": "delight.refreshed",
                            "phase": "ready",
                            "count": net_new_delights,
                            "total_pending": delight_count_after,
                            "message": (
                                f"刚发现 {net_new_delights} 条新的惊喜推荐"
                                if net_new_delights > 1
                                else "刚发现一条新的惊喜推荐"
                            ),
                        }
                    )
            await asyncio.sleep(self.proactive_push_interval_seconds)

    async def _loop_image_cache_cleanup(self) -> None:
        """Periodically prune the cover-image disk cache.

        Evicts cached covers of consumed, unsaved content (the user has seen and
        passed on them, and they are not in favorites / watch-later). Covers of
        saved or still-pending content are kept, and un-refetchable covers (XHS
        rotating-token URLs) are protected — the cached copy is their only durable
        source once the upstream token expires. The bulk first pass runs at API
        startup; this is the steady-state sweep.
        """
        while True:
            await asyncio.sleep(_IMAGE_CACHE_CLEANUP_INTERVAL_SECONDS)
            try:
                result = cleanup_image_cache(database=self.database)
            except Exception:
                logger.debug("image cache cleanup tick failed", exc_info=True)
                continue
            if result.removed:
                logger.info(
                    "image cache cleanup: removed %d cover files (%.1f MB freed; "
                    "%d consumed, %d aged orphans, %d unrefetchable protected)",
                    result.removed,
                    result.freed_bytes / (1024 * 1024),
                    result.removed_consumed,
                    result.removed_aged_orphans,
                    result.protected_unrefetchable,
                )

    async def _prefetch_uncached_covers(
        self,
        *,
        scan: int = _COVER_PREFETCH_SCAN,
        max_fetch: int = _COVER_PREFETCH_MAX_FETCH,
    ) -> int:
        """Cache covers for recently discovered, still-servable content.

        Fixes the «封面 502» failure mode: cover images were previously fetched only
        when a card was displayed, by which point a short-lived XHS signed token had
        often expired. Prefetching right after discovery saves the image while the
        token is fresh. Un-refetchable (XHS rotating-token) covers are tried first
        since re-fetchable ones (Bilibili etc.) never expire. Best-effort and bounded.
        """
        candidates = self.database.iter_servable_cover_urls(
            recent_hours=_COVER_PREFETCH_RECENT_HOURS,
            limit=scan,
        )
        targets = select_prefetch_targets(candidates, max_fetch=max_fetch)
        fetched = 0
        for url in targets:
            if await prefetch_cover(url):
                fetched += 1
        return fetched

    async def _loop_cover_prefetch(self) -> None:
        """Periodically cache discovered covers while their CDN token is fresh."""
        while True:
            try:
                cached = await self._prefetch_uncached_covers()
            except Exception:
                logger.debug("cover prefetch tick failed", exc_info=True)
                cached = 0
            if cached:
                logger.info("cover prefetch: cached %d new covers", cached)
            await asyncio.sleep(_COVER_PREFETCH_INTERVAL_SECONDS)

    async def _tick_xhs_producer(self) -> None:
        """Invoke the xhs search task producer if one is configured."""
        producer = self.xhs_producer
        if producer is None:
            return
        deficit = self._source_deficit("xiaohongshu")
        if deficit <= 0:
            return
        limit = max(1, min(deficit, self.discovery_limit))
        produce_fn = getattr(producer, "produce_if_due", None)
        if not callable(produce_fn):
            return
        if _call_accepts_limit(produce_fn):
            await produce_fn(limit=limit)
        else:
            await produce_fn()

    async def _tick_douyin_producer(self) -> None:
        """Invoke the Douyin discovery producer if Douyin is under quota."""
        producer = self.douyin_producer
        if producer is None:
            return
        if not self._is_initialized():
            return
        deficit = self._source_deficit("douyin")
        if deficit <= 0:
            return
        produce_fn = getattr(producer, "produce_if_due", None)
        if not callable(produce_fn):
            return
        limit = max(1, min(deficit, self.discovery_limit))
        if _call_accepts_limit(produce_fn):
            await produce_fn(limit=limit)
        else:
            await produce_fn()

    async def _tick_youtube_producer(self) -> None:
        """Invoke the YouTube discovery producer if YouTube is under quota."""
        producer = self.youtube_producer
        if producer is None:
            return
        if not self._is_initialized():
            return
        deficit = self._source_deficit("youtube")
        if deficit <= 0:
            return
        produce_fn = getattr(producer, "produce_if_due", None)
        if not callable(produce_fn):
            return
        limit = max(1, min(deficit, self.discovery_limit))
        if _call_accepts_limit(produce_fn):
            await produce_fn(limit=limit)
        else:
            await produce_fn()

    async def _tick_soul_pipeline(self) -> None:
        """Invoke ProfileUpdatePipeline.tick() if the soul engine exposes one.

        Splitting this into a helper makes it cheap to call from tests
        and from a manual single-iteration loop runner.
        """
        pipeline = getattr(self.soul_engine, "pipeline", None)
        if pipeline is None:
            return
        tick_fn = getattr(pipeline, "tick", None)
        if not callable(tick_fn):
            return
        await tick_fn()

    def _pending_signal_events_count(self, state: dict[str, object]) -> int:
        return len(
            self.database.query_events_since(
                after_event_id=self._int_state_value(state, "last_processed_event_id"),
                event_types=self._signal_event_types,
            )
        )

    def _build_refresh_plan(
        self,
        state: dict[str, object],
    ) -> list[tuple[list[str], int]]:
        pending_events = self._pending_signal_events_count(state)
        pool_available = self.database.count_pool_candidates(
            xhs_self_nickname=self._xhs_self_nickname()
        )
        pool_below_target = pool_available < self.pool_target_count

        if pool_below_target:
            source_plan = self._build_source_replenishment_plan()
            if source_plan:
                return source_plan
            # When Bilibili is already at its platform quota, the missing
            # capacity belongs to enabled non-Bilibili platform producers.
            # Running the Bilibili fallback here would immediately violate
            # the configured pool-source ratio.
            return []

        if "bilibili" not in self._normalized_pool_source_shares():
            return []

        plan: list[tuple[list[str], int]] = []
        if pending_events >= self.signal_event_threshold:
            plan.append((["search", "related_chain"], self.discovery_limit))
        if self._is_due(
            str(state.get("last_trending_refresh_at", "")),
            hours=self.trending_refresh_hours,
        ):
            plan.append((["trending"], self.discovery_limit))
        if self._is_due(
            str(state.get("last_explore_refresh_at", "")),
            hours=self.explore_refresh_hours,
        ):
            plan.append((["explore"], self.discovery_limit))
        return plan

    async def refresh_after_event_ingest(self) -> dict[str, object]:
        """Opportunistically refresh after new events arrive."""
        return await self.refresh_if_needed()

    async def refresh_after_feedback(self) -> dict[str, object]:
        """Opportunistically refresh after explicit feedback."""
        return await self.refresh_if_needed()

    async def refresh_after_init(self) -> dict[str, object]:
        """Allow callers to trigger a refresh immediately after initialization."""
        return await self.refresh_if_needed()

    async def drain_discovery_candidates_once(
        self,
        *,
        batch_size: int | None = None,
    ) -> dict[str, int]:
        """Drain one pending discovery-candidate batch through the shared evaluator."""

        pipeline = self.discovery_candidate_pipeline
        if pipeline is None:
            return {"evaluated": 0, "cached": 0, "rejected": 0}
        if self._discovery_drain_lock.locked():
            return {"evaluated": 0, "cached": 0, "rejected": 0}
        async with self._discovery_drain_lock:
            try:
                pool_available = self.database.count_pool_candidates(
                    xhs_self_nickname=self._xhs_self_nickname()
                )
            except TypeError:
                pool_available = self.database.count_pool_candidates()
            if int(pool_available) >= self.pool_target_count:
                return {"evaluated": 0, "cached": 0, "rejected": 0}
            try:
                profile = await self.soul_engine.get_profile()
            except Exception as exc:
                logger.info("discovery candidate drain skipped: soul profile unavailable: %s", exc)
                return {"evaluated": 0, "cached": 0, "rejected": 0}
            if profile is None:
                logger.info("discovery candidate drain skipped: soul profile unavailable")
                return {"evaluated": 0, "cached": 0, "rejected": 0}
            result = await pipeline.drain_pending(
                profile=profile,
                batch_size=batch_size or self.discovery_limit,
            )
            return cast("dict[str, int]", result)

    async def _complete_manual_refresh(self) -> None:
        try:
            refresh_result = await self.force_refresh()
        except Exception as exc:
            self._manual_refresh_state = "failed"
            self._manual_refresh_message = f"这次补货没跑通：{exc}"
            self._manual_refresh_finished_at = self._now().isoformat()
            await self._publish_event(
                {
                    "type": "refresh.failed",
                    "phase": "failed",
                    "message": self._manual_refresh_message,
                    **self._pool_count_payload(self._pool_readiness_counts()),
                }
            )
            return
        self._manual_refresh_state = "success"
        if bool(refresh_result.get("refreshed")):
            runtime_state = self.memory_manager.load_discovery_runtime_state()
            last_discovered = self._int_state_value(runtime_state, "last_discovered_count")
            last_replenished = self._int_state_value(runtime_state, "last_replenished_count")
        else:
            last_discovered = 0
            last_replenished = 0
        self._manual_refresh_message = (
            "刚给你补了一批新的。"
            if last_replenished > 0
            else (
                "这轮找到了内容，但可立即换的库存没变。"
                if last_discovered > 0
                else "这轮没补进新的候选。"
            )
        )
        self._manual_refresh_finished_at = self._now().isoformat()
        await self._publish_event(
            {
                "type": "refresh.pool_updated",
                "phase": "done",
                "message": self._manual_refresh_message,
                **self._pool_count_payload(self._pool_readiness_counts()),
            }
        )

    async def _run_refresh_plan(
        self,
        *,
        state: dict[str, object],
        profile: Any,
        plan: list[tuple[list[str], int]],
        reason: str,
    ) -> dict[str, object]:
        before_pool_counts = self._pool_readiness_counts()
        before_pool_count = before_pool_counts["available"]
        initial_pool_below_target = before_pool_count < self.pool_target_count
        all_discovered: list[Any] = []
        pipeline_discovered_count = 0
        flattened_strategies: list[str] = []
        replenished_topics: list[str] = []
        # v0.3.47+: per-strategy expression precompute tasks. Each strategy's
        # `discover()` blocks on a slow LLM eval batch (8-16 minutes
        # observed in production). Without this, popup copy precompute was
        # gated until ALL strategies finished — i.e. ~30 min of latency
        # for fresh items. Now: as soon as a strategy yields content we
        # kick a precompute task; ``self._precompute_lock`` inside
        # ``RecommendationEngine`` serialises them so two tasks don't
        # double-spend LLM tokens on the same un-precomputed candidates.
        precompute_tasks: list[asyncio.Task[Any]] = []

        await self._publish_event(
            {
                "type": "refresh.started",
                "phase": "running",
                "message": "开始给你补候选了",
                **self._pool_count_payload(before_pool_counts),
            }
        )

        for strategies, requested_limit in plan:
            current_pool_counts = self._pool_readiness_counts()
            current_pool_count = current_pool_counts["available"]
            if current_pool_count >= self.pool_target_count:
                break

            await self._publish_event(
                {
                    "type": "refresh.strategy",
                    "phase": "running",
                    "strategy": "+".join(strategies),
                    "message": self._strategy_message(strategies),
                    **self._pool_count_payload(current_pool_counts),
                }
            )

            effective_limit = self._requested_refresh_limit(
                requested_limit=requested_limit,
                current_pool_count=current_pool_count,
                pool_below_target=initial_pool_below_target,
            )
            strategy_limits = self._requested_strategy_limits(
                strategies=strategies,
                requested_limit=requested_limit,
                effective_limit=effective_limit,
                current_pool_count=current_pool_count,
                pool_below_target=initial_pool_below_target,
            )
            try:
                pool_snapshot = build_pool_distribution_snapshot(
                    self.database,
                    pool_target_count=self.pool_target_count,
                    source_targets=self._source_target_counts(),
                )
            except Exception:
                logger.exception("Failed to build pool distribution snapshot")
                pool_snapshot = None
            pipeline = self.discovery_candidate_pipeline
            discovered: list[Any] = []
            topic_items: list[Any] = []
            discovered_count = 0
            admitted_count = 0
            if pipeline is not None:
                produced_count = await pipeline.produce_and_enqueue(
                    profile=profile,
                    strategies=strategies,
                    limit=effective_limit,
                    strategy_limits=strategy_limits,
                    pool_snapshot=pool_snapshot,
                )
                drain_result = await pipeline.drain_pending(
                    profile=profile,
                    batch_size=effective_limit,
                )
                discovered_count = int(produced_count or 0)
                admitted_count = int(drain_result.get("cached", 0) or 0)
                if admitted_count > 0:
                    topic_items = list(getattr(pipeline, "last_admitted_items", []) or [])
                pipeline_discovered_count += discovered_count
            else:
                discover_fn = self.discovery_engine.discover
                discover_kwargs: dict[str, Any] = {
                    "strategies": strategies,
                    "limit": effective_limit,
                }
                if strategy_limits and _call_accepts_strategy_limits(discover_fn):
                    discover_kwargs["strategy_limits"] = strategy_limits
                if _call_accepts_pool_snapshot(discover_fn):
                    discover_kwargs["pool_snapshot"] = pool_snapshot
                discovered = await discover_fn(profile, **discover_kwargs)
                topic_items = discovered
                discovered_count = len(discovered)
                admitted_count = discovered_count
            all_discovered.extend(discovered)
            flattened_strategies.extend(strategies)

            if admitted_count > 0:
                replenished_topics.extend(self._extract_topics(topic_items))
                # Fire expression precompute now (in parallel with the next
                # strategy's discovery LLM call). The lock inside the engine
                # queues this if a previous task is still running.
                precompute_tasks.append(
                    self._track_task(
                        "precompute_pool_copy",
                        self._safe_precompute_pool_copy(profile=profile),
                    )
                )

        if flattened_strategies:
            self.database.trim_explore_cluster_overflow(max_per_cluster=3)
            # Cap each topic_group at ~10% of pool target so a single hot
            # topic (e.g. 人工智能 from related_chain) can't accumulate
            # hundreds of fresh candidates across rounds and starve other
            # sources/topics. Floor at 3 to keep small pools usable.
            self.database.trim_topic_group_overflow(
                max_per_group=max(3, self.pool_target_count // 10),
            )
            self.database.evict_stale_pool_items(max_age_days=14)
            # Snapshot delight count BEFORE precompute so we can detect
            # net new above-threshold delights and push a refresh event
            # to the popup (no per-item chrome notification — popup
            # re-fetches /api/delight/pending-batch when this fires).
            delight_count_before = self._safe_count_delight_candidates()
            # v0.3.47+: drain the per-strategy precompute tasks fired
            # eagerly above. They have already been running in parallel
            # with discovery's later strategies, so this awaits whatever
            # is still pending instead of starting from scratch. If the
            # discovery loop produced nothing precompute-eligible (e.g.
            # all rejected at eval), fall back to one synchronous call so
            # any earlier-cycle backlog still gets cleared.
            if precompute_tasks:
                await asyncio.gather(*precompute_tasks, return_exceptions=True)
            else:
                await self._safe_precompute_pool_copy(profile=profile)
            # Pre-warm supergroup-merge embeddings so the popup's "换一批"
            # hot path always hits the L1/L2 cache. New labels added by
            # this refresh round get warmed before the user clicks.
            # Warm embedding-derived caches in the background. They are
            # latency optimizations for later serve() calls, not
            # requirements for this refresh result to become visible.
            # Keeping them off the refresh lock prevents slow local
            # embedding backends from leaving the popup stuck at "正在补货".
            self._track_task(
                "prewarm_supergroup_embeddings",
                self._safe_prewarm_supergroup_embeddings(),
            )
            self._track_task(
                "prewarm_pool_mmr_embeddings",
                self._safe_prewarm_pool_mmr_embeddings(),
            )
            delight_count_after = self._safe_count_delight_candidates()
            net_new_delights = max(0, delight_count_after - delight_count_before)
            if net_new_delights > 0:
                await self._publish_event(
                    {
                        "type": "delight.refreshed",
                        "phase": "ready",
                        "count": net_new_delights,
                        "total_pending": delight_count_after,
                        "message": (
                            f"刚发现 {net_new_delights} 条新的惊喜推荐"
                            if net_new_delights > 1
                            else "刚发现一条新的惊喜推荐"
                        ),
                    }
                )
            await self._publish_delight_if_available()
            await self._publish_probe_if_available()

            # v0.3.66+: enforce the absolute pool cap at the end of every
            # refresh plan. The earlier trim_topic_group_overflow /
            # trim_explore_cluster_overflow / evict_stale calls only bound
            # per-axis concentration (topic, cluster, age) — none of them
            # cap the total count. Long-running discovery cycles (10-30
            # min for the LLM eval batch) also block the periodic
            # _enforce_pool_cap tick in run_forever, so the popup
            # routinely saw pool_available_count drift well past
            # pool_target_count (e.g. 668 with target=600 in production).
            # _enforce_pool_cap also runs reactivate_under_quota and
            # source-share-aware trim, so this is the right place to land
            # the freshly-discovered items into their final shape before
            # the popup re-fetches.
            try:
                self._enforce_pool_cap()
            except Exception:
                logger.exception("post-refresh enforce_pool_cap failed")

        now = self._now().isoformat()
        latest_event_id = self.database.get_latest_event_id()
        if "search" in flattened_strategies or "related_chain" in flattened_strategies:
            state["last_event_refresh_at"] = now
            state["last_processed_event_id"] = latest_event_id
        if "trending" in flattened_strategies:
            state["last_trending_refresh_at"] = now
        if "explore" in flattened_strategies:
            state["last_explore_refresh_at"] = now
        after_pool_counts = self._pool_readiness_counts()
        after_pool_count = after_pool_counts["available"]
        state["last_discovered_count"] = len(all_discovered) + pipeline_discovered_count
        state["last_replenished_count"] = max(0, after_pool_count - before_pool_count)
        if replenished_topics:
            state["recent_pool_topics"] = self._dedupe_topics(replenished_topics)[:3]
        self.memory_manager.save_discovery_runtime_state(state)
        discovered_count = self._int_state_value(state, "last_discovered_count")
        replenished_count = self._int_state_value(state, "last_replenished_count")
        await self._publish_event(
            {
                "type": "refresh.pool_updated",
                "phase": "done",
                "message": (
                    f"刚补进 {replenished_count} 条新的"
                    if replenished_count > 0
                    else (
                        "这轮找到了内容，但可立即换的库存没变"
                        if discovered_count > 0
                        else "这轮没补进新的候选"
                    )
                ),
                **self._pool_count_payload(after_pool_counts),
                "last_discovered_count": discovered_count,
                "last_replenished_count": replenished_count,
                "recent_pool_topics": self._list_state_value(state, "recent_pool_topics"),
            }
        )
        return {
            "refreshed": bool(flattened_strategies),
            "strategies": flattened_strategies,
            "reason": reason,
            "recommendation_count": 0,
        }

    async def _publish_pool_status_if_changed(self) -> None:
        """Emit a ``pool_status`` runtime event when the pool count rotates.

        Pool count changes most often via ``enforce_pool_cap`` reactivating
        suppressed items or trimming overflow — a path that doesn't go
        through the end-of-refresh ``refresh.pool_updated`` event. Without
        this hook, the popup's pool-count UI only refreshes when a full
        refresh wave completes; now it stays in sync within seconds of any
        pool-state change.

        Only emits when the count is different from the last emit, so
        steady-state ticks don't spam the WebSocket stream.
        """
        try:
            pool_counts = self._pool_readiness_counts()
            current = int(pool_counts["available"])
        except Exception:
            return
        if current == self._last_published_pool_count:
            return
        self._last_published_pool_count = current
        await self._publish_event(
            {
                "type": "pool_status",
                **self._pool_count_payload(pool_counts),
                "pool_target_count": int(self.pool_target_count),
            }
        )

    def _safe_count_delight_candidates(self) -> int:
        """Best-effort count of pending delight candidates (returns 0 on any
        error so the caller can do delta-based comparison without crashing
        the refresh tick)."""
        from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD

        try:
            return int(
                self.database.count_delight_candidates(min_delight_score=DEFAULT_DELIGHT_THRESHOLD)
            )
        except Exception:
            return 0

    async def _publish_event(self, event: dict[str, object]) -> bool:
        publish = getattr(self.event_hub, "publish", None)
        if callable(publish):
            result = await publish(event)
            return True if result is None else bool(result)
        return False

    async def _publish_delight_if_available(self) -> None:
        """Check for a pending delight candidate and push it via WebSocket."""
        candidate = self.get_pending_delight()
        if candidate is None:
            return
        await self._publish_event(
            {
                "type": "delight.candidate",
                "phase": "ready",
                "message": "发现了一条你可能会意外喜欢的内容",
                "bvid": candidate.get("bvid", ""),
                "title": candidate.get("title", ""),
                "delight_reason": candidate.get("delight_reason", ""),
                "delight_score": candidate.get("delight_score", 0.0),
                "delight_hook": candidate.get("delight_hook", ""),
                "cover_url": candidate.get("cover_url", ""),
                "content_url": candidate.get("content_url", ""),
                "source_platform": candidate.get("source_platform", "bilibili"),
            }
        )

    _PROBE_COOLDOWN_HOURS = 4  # Don't re-push the same domain within this window

    async def _publish_interest_probe_if_available(self) -> bool:
        """Push the top speculative-interest hypothesis via WebSocket.

        Fires an ``interest.probe`` event when the speculator has an active
        hypothesis that the agent should ask the user to confirm.

        De-duplication: each domain is pushed at most once per cooldown
        window (``_PROBE_COOLDOWN_HOURS``).  Already-probed domains are
        tracked in ``discovery_runtime_state["probed_domains"]``.
        """
        speculator = getattr(self.soul_engine, "_speculator", None)
        get_active = getattr(speculator, "get_active_speculations", None)
        if not callable(get_active):
            return False
        specs = list(get_active())
        if not specs:
            return False

        # Load probe history from runtime state
        state = self.memory_manager.load_discovery_runtime_state()
        probed: dict[str, str] = state.get("probed_domains", {})  # type: ignore[assignment]
        probed_axes: dict[str, str] = state.get("probed_axes", {})  # type: ignore[assignment]
        probed_distance_bands: dict[str, str] = state.get("probed_distance_bands", {})  # type: ignore[assignment]
        # Purge expired entries
        now = self._now()
        cutoff = (now - timedelta(hours=self._PROBE_COOLDOWN_HOURS)).isoformat()
        probed = {d: t for d, t in probed.items() if t > cutoff}
        probed_axes = {axis: t for axis, t in probed_axes.items() if t > cutoff}
        probed_distance_bands = {mode: t for mode, t in probed_distance_bands.items() if t > cutoff}

        top = choose_next_probe_candidate(
            specs,
            probed_domains=set(probed),
            probed_axes=set(probed_axes),
            probed_probe_modes=set(probed_distance_bands),
            feedback_history=state.get("probe_feedback_history", []),
        )
        if top is None:
            return False  # All active specs were probed recently

        domain = str(getattr(top, "domain", "")).strip()
        if not domain:
            return False

        probe_mode = _normalize_probe_mode(getattr(top, "probe_mode", ""))
        challenge = probe_mode in _PROBE_CHALLENGE_MODES
        with suppress(Exception):
            challenge = challenge or bool(getattr(top, "challenge", False))
        axis = build_probe_axis(
            experience_mode=getattr(top, "experience_mode", ""),
            entry_load=getattr(top, "entry_load", ""),
        )
        reason = str(getattr(top, "reason", "")).strip()
        specifics = [
            str(getattr(item, "name", "")).strip()
            for item in getattr(top, "specifics", [])
            if str(getattr(item, "name", "")).strip()
        ][:5]
        specific_hint = ""
        if specifics:
            specific_hint = "（比如：" + "、".join(specifics[:3]) + "）"
        question = (
            f"我从你最近的轨迹里嗅到你可能对【{domain}】{specific_hint}感兴趣"
            f"——{reason} 这个方向你自己认不认？"
            if reason
            else f"我感觉你可能对【{domain}】{specific_hint}有潜在兴趣，这个方向你自己认不认？"
        )
        delivered = await self._publish_event(
            {
                "type": "interest.probe",
                "phase": "ready",
                "message": "有一个猜测兴趣方向想确认",
                "domain": domain,
                "category": str(getattr(top, "category", "")),
                "reason": reason,
                "confidence": float(getattr(top, "confidence", 0.0) or 0.0),
                "weight": float(getattr(top, "weight", 0.0) or 0.0),
                "experience_mode": str(getattr(top, "experience_mode", "")),
                "entry_load": str(getattr(top, "entry_load", "")),
                "probe_mode": probe_mode,
                "challenge": challenge,
                "specifics": specifics,
                "question": question,
            }
        )
        if not delivered:
            logger.debug("interest probe skipped: no runtime-stream subscriber")
            return False

        # Record this probe only after it has reached at least one runtime stream.
        probed[domain.lower()] = now.isoformat()
        state["probed_domains"] = probed
        if axis:
            probed_axes[axis] = now.isoformat()
        state["probed_axes"] = probed_axes
        probed_distance_bands[probe_mode] = now.isoformat()
        state["probed_distance_bands"] = probed_distance_bands
        self.memory_manager.save_discovery_runtime_state(state)
        return True

    async def _publish_avoidance_probe_if_available(self) -> bool:
        """Push the top speculative-avoidance hypothesis via WebSocket."""
        speculator = getattr(self.soul_engine, "_avoidance_speculator", None)
        get_active = getattr(speculator, "get_active_avoidances", None)
        if not callable(get_active):
            return False
        avoidances = list(get_active())
        if not avoidances:
            return False

        state = self.memory_manager.load_discovery_runtime_state()
        probed: dict[str, str] = state.get("probed_avoidance_domains", {})  # type: ignore[assignment]
        probed_axes: dict[str, str] = state.get("probed_avoidance_axes", {})  # type: ignore[assignment]
        now = self._now()
        cutoff = (now - timedelta(hours=self._PROBE_COOLDOWN_HOURS)).isoformat()
        probed = {d: t for d, t in probed.items() if t > cutoff}
        probed_axes = {axis: t for axis, t in probed_axes.items() if t > cutoff}

        top = choose_next_avoidance_candidate(
            avoidances,
            probed_domains=set(probed),
            probed_axes=set(probed_axes),
            feedback_history=state.get("avoidance_probe_feedback_history", []),
        )
        if top is None:
            return False

        domain = str(getattr(top, "domain", "")).strip()
        if not domain:
            return False

        axis = build_probe_axis(
            experience_mode=getattr(top, "experience_mode", ""),
            entry_load=getattr(top, "entry_load", ""),
        )
        reason = str(getattr(top, "reason", "")).strip()
        specifics = [
            str(getattr(item, "name", "")).strip()
            for item in getattr(top, "specifics", [])
            if str(getattr(item, "name", "")).strip()
        ][:5]
        specific_hint = ""
        if specifics:
            specific_hint = "（比如：" + "、".join(specifics[:3]) + "）"
        question = (
            f"我猜【{domain}】{specific_hint}可能是你想避开的方向——{reason} 这个判断准不准？"
            if reason
            else f"我感觉【{domain}】{specific_hint}可能不是你想看的方向，这个判断准不准？"
        )
        delivered = await self._publish_event(
            {
                "type": "avoidance.probe",
                "phase": "ready",
                "message": "有一个可能想避开的方向想确认",
                "domain": domain,
                "reason": reason,
                "confidence": float(getattr(top, "confidence", 0.0) or 0.0),
                "weight": float(getattr(top, "weight", 0.0) or 0.0),
                "source_mode": str(getattr(top, "source_mode", "")),
                "source_signal": str(getattr(top, "source_signal", "")),
                "experience_mode": str(getattr(top, "experience_mode", "")),
                "entry_load": str(getattr(top, "entry_load", "")),
                "specifics": specifics,
                "question": question,
            }
        )
        if not delivered:
            logger.debug("avoidance probe skipped: no runtime-stream subscriber")
            return False

        probed[domain.lower()] = now.isoformat()
        state["probed_avoidance_domains"] = probed
        if axis:
            probed_axes[axis] = now.isoformat()
        state["probed_avoidance_axes"] = probed_axes
        self.memory_manager.save_discovery_runtime_state(state)
        return True

    async def _publish_probe_if_available(self) -> bool:
        """Publish at most one proactive probe, alternating interest and avoidance."""
        state = self.memory_manager.load_discovery_runtime_state()
        last_kind = str(state.get("last_probe_kind", "")).strip().lower()
        order = (
            ("avoidance", self._publish_avoidance_probe_if_available),
            ("interest", self._publish_interest_probe_if_available),
        )
        if last_kind != "interest":
            order = (
                ("interest", self._publish_interest_probe_if_available),
                ("avoidance", self._publish_avoidance_probe_if_available),
            )

        for kind, publish in order:
            delivered = await publish()
            if not delivered:
                continue
            latest_state = self.memory_manager.load_discovery_runtime_state()
            latest_state["last_probe_kind"] = kind
            self.memory_manager.save_discovery_runtime_state(latest_state)
            return True
        return False

    def _strategy_message(self, strategies: list[str]) -> str:
        if strategies == ["search", "related_chain"]:
            return "先从你刚刚的口味里搜一轮"
        if strategies == ["trending"]:
            return "顺手看看站内热榜里有没有你会吃的"
        if strategies == ["explore"]:
            return "再给你探一点你可能会意外喜欢的"
        return "正在继续给你补候选"

    def _build_source_replenishment_plan(self) -> list[tuple[list[str], int]]:
        source_available_counts = self._count_pool_available_candidates_by_source()
        source_raw_counts = self._count_pool_raw_material_by_source()
        target_counts = self._source_target_counts()
        raw_target_counts = self._raw_source_target_counts()
        plan: list[tuple[list[str], int]] = []
        for source in _PLATFORM_SOURCE_ORDER:
            requested = self._source_requested_count(
                source,
                source_available_counts=source_available_counts,
                source_raw_counts=source_raw_counts,
                target_counts=target_counts,
                raw_target_counts=raw_target_counts,
            )
            if requested <= 0:
                continue
            if source == "bilibili":
                # Bilibili is a platform quota now, but its implementation
                # still fans out through four established strategy names.
                plan.append((list(_BILIBILI_DISCOVERY_SOURCES), requested))
        return plan

    def _raw_material_ceiling(self) -> int:
        return max(self.pool_target_count * 2, self.pool_target_count + 120)

    def _source_target_counts(self, *, total: int | None = None) -> dict[str, int]:
        target_total = self.pool_target_count if total is None else max(0, int(total))
        shares = self._normalized_pool_source_shares()
        total_share = sum(shares.values())
        remaining = target_total
        targets: dict[str, int] = {}
        items = list(shares.items())
        for index, (source, share) in enumerate(items):
            if index == len(items) - 1:
                targets[source] = remaining
                break
            count = round(target_total * share / total_share)
            count = min(remaining, count)
            targets[source] = count
            remaining -= count
        return targets

    def _raw_source_target_counts(self) -> dict[str, int]:
        return self._source_target_counts(total=self._raw_material_ceiling())

    def _source_deficit(self, source_family: str) -> int:
        return self._source_requested_count(source_family)

    def _source_requested_count(
        self,
        source_family: str,
        *,
        source_available_counts: dict[str, int] | None = None,
        source_raw_counts: dict[str, int] | None = None,
        target_counts: dict[str, int] | None = None,
        raw_target_counts: dict[str, int] | None = None,
    ) -> int:
        if source_available_counts is None:
            source_available_counts = self._count_pool_available_candidates_by_source()
        if source_raw_counts is None:
            source_raw_counts = self._count_pool_raw_material_by_source()
        if target_counts is None:
            target_counts = self._source_target_counts()
        if raw_target_counts is None:
            raw_target_counts = self._raw_source_target_counts()

        available_target = int(target_counts.get(source_family, 0))
        current_available = self._platform_source_count(source_available_counts, source_family)
        available_deficit = max(0, available_target - current_available)
        try:
            current_global_available = self.database.count_pool_candidates(
                xhs_self_nickname=self._xhs_self_nickname()
            )
        except TypeError:
            current_global_available = self.database.count_pool_candidates()
        global_available_deficit = max(0, self.pool_target_count - int(current_global_available))
        raw_target = int(raw_target_counts.get(source_family, 0))
        current_raw = self._platform_source_count(source_raw_counts, source_family)
        raw_headroom = max(0, raw_target - current_raw)
        return max(0, min(available_deficit, raw_headroom, global_available_deficit))

    def _count_pool_available_candidates_by_source(self) -> dict[str, int]:
        count_fn = getattr(self.database, "count_pool_available_candidates_by_source", None)
        if callable(count_fn):
            try:
                counts = count_fn(xhs_self_nickname=self._xhs_self_nickname())
            except TypeError:
                counts = count_fn()
            return {str(source): int(count) for source, count in dict(counts).items()}
        self._warn_pool_count_fallback_once("available_by_source")
        return self.database.count_pool_candidates_by_source()

    def _count_pool_raw_material_by_source(self) -> dict[str, int]:
        count_fn = getattr(self.database, "count_pool_raw_material_by_source", None)
        if callable(count_fn):
            counts = count_fn()
            return {str(source): int(count) for source, count in dict(counts).items()}
        self._warn_pool_count_fallback_once("raw_material_by_source")
        return self.database.count_pool_candidates_by_source()

    def _warn_pool_count_fallback_once(self, key: str) -> None:
        if key in self._warned_pool_count_fallbacks:
            return
        self._warned_pool_count_fallbacks.add(key)
        logger.warning(
            "pool source count fallback used for %s; production should expose available/raw "
            "source counters to avoid raw-count deadlocks",
            key,
        )

    def _platform_source_count(self, source_counts: dict[str, int], source_family: str) -> int:
        if source_family == "bilibili":
            if "bilibili" in source_counts:
                return int(source_counts.get("bilibili", 0))
            return sum(int(source_counts.get(source, 0)) for source in _BILIBILI_DISCOVERY_SOURCES)
        return int(source_counts.get(source_family, 0))

    def _warn_on_stranded_source_shares(self) -> None:
        """Warn once at startup if any configured share has no producer.

        ``runtime.source_policy.effective_pool_source_shares`` already strips
        sources whose ``enabled`` flag is False, so a stranded share here
        means the user kept the source on but the matching producer is
        not wired (missing build_*_producer, scheduler.enabled=False, …).
        Without this warning the pool sits below ``pool_target_count``
        forever and the missing slack is invisible.
        """
        shares = self._normalized_pool_source_shares()
        targets = self._source_target_counts()
        stranded: list[str] = []
        for source, target in targets.items():
            if target <= 0:
                continue
            if source == "bilibili":
                continue  # always served by the four discovery strategies
            if source == "xiaohongshu" and self.xhs_producer is None:
                stranded.append("xiaohongshu")
            elif source == "douyin" and self.douyin_producer is None:
                stranded.append("douyin")
            elif source == "youtube" and self.youtube_producer is None:
                stranded.append("youtube")
            elif source not in {"bilibili", "xiaohongshu", "douyin", "youtube"}:
                # Unknown source family with an explicit share.
                stranded.append(source)
        if stranded:
            logger.warning(
                "pool_source_shares allocate quota to sources without an "
                "active producer (will leave pool under target): sources=%s "
                "shares=%s",
                stranded,
                {s: shares.get(s) for s in stranded},
            )

    def _normalized_pool_source_shares(self) -> dict[str, int]:
        raw = self.pool_source_shares or _DEFAULT_PLATFORM_SOURCE_SHARES
        normalized: dict[str, int] = {}
        for source in _PLATFORM_SOURCE_ORDER:
            try:
                share = int(raw.get(source, 0))
            except (TypeError, ValueError):
                share = 0
            if share > 0:
                normalized[source] = share
        for source, raw_share in raw.items():
            source_key = str(source).strip().lower()
            if not source_key or source_key in normalized:
                continue
            try:
                share = int(raw_share)
            except (TypeError, ValueError):
                continue
            if share > 0:
                normalized[source_key] = share
        return normalized or dict(_DEFAULT_PLATFORM_SOURCE_SHARES)

    def _requested_refresh_limit(
        self,
        *,
        requested_limit: int,
        current_pool_count: int,
        pool_below_target: bool,
    ) -> int:
        """Decide how many candidates a grouped discovery call should target.

        v0.3.24+ pool-aware sizing. Pre-fix this enforced an absolute
        floor of ``discovery_limit`` (30) per grouped call, even when the
        pool was 595/600 and only needed 5 more items. With 4 strategies
        × 30 = 120 candidates LLM-evaluated per refresh — and the
        suppress-pass keeping only ~20 — that meant ~80% of LLM
        evaluation cost went to candidates that were immediately
        suppressed. The fix sizes each strategy's limit to the smaller
        of total pool gap and requested source gap (with 1.5x oversample
        for items below score threshold and a floor of 5 to keep
        grouped call productive on tiny gaps), capped by ``discovery_limit``
        so a sudden post-init replenish doesn't turn into a single huge
        wave.
        """
        if pool_below_target:
            total_gap = max(0, self.pool_target_count - current_pool_count)
            requested_gap = max(1, int(requested_limit))
            gap = min(total_gap, requested_gap)
            # The 2-phase plan dispatches strategies in groups; per-
            # strategy target is roughly gap // (typical strategy count
            # per phase = 2), with a 1.5x oversample for threshold
            # filtering. Floor at 5 so a strategy that only finds 2
            # interesting items doesn't starve the pool entirely.
            per_strategy_target = max(5, gap * 3 // 4)
            # Cap at discovery_limit to preserve original behaviour
            # when the gap is huge (e.g. fresh init, just-trimmed pool).
            effective_limit = min(self.discovery_limit, per_strategy_target)
        else:
            effective_limit = max(self.discovery_limit, requested_limit)
        return min(_MAX_DISCOVERY_BACKFILL_PER_REFRESH, max(1, effective_limit))

    def _requested_strategy_limits(
        self,
        *,
        strategies: list[str],
        requested_limit: int,
        effective_limit: int,
        current_pool_count: int,
        pool_below_target: bool,
    ) -> dict[str, int] | None:
        """Split a grouped Bilibili refresh budget across its strategies."""
        if not pool_below_target or len(strategies) <= 1:
            return None
        if not all(strategy in _BILIBILI_DISCOVERY_SOURCES for strategy in strategies):
            return None
        total_gap = max(1, self.pool_target_count - current_pool_count)
        shared_budget = min(
            max(1, int(requested_limit)),
            max(1, int(effective_limit)),
            total_gap,
        )
        return self._split_budget_across_strategies(strategies, shared_budget)

    @staticmethod
    def _split_budget_across_strategies(
        strategies: list[str],
        budget: int,
    ) -> dict[str, int]:
        if not strategies:
            return {}
        safe_budget = max(0, int(budget))
        base, extra = divmod(safe_budget, len(strategies))
        return {
            strategy: base + (1 if index < extra else 0)
            for index, strategy in enumerate(strategies)
        }

    def _is_initialized(self) -> bool:
        try:
            soul_layer = self.memory_manager.get_layer("soul")
        except Exception:
            return False
        data = getattr(soul_layer, "data", {})
        return isinstance(data, dict) and bool(data)

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime | None:
        if not value:
            return None
        with suppress(ValueError):
            return datetime.fromisoformat(value)
        return None

    @staticmethod
    def _int_state_value(state: dict[str, object], key: str) -> int:
        value = state.get(key, 0)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            with suppress(ValueError):
                return int(value)
        return 0

    def _is_due(self, value: str, *, hours: int) -> bool:
        if hours <= 0:
            return True
        last_run = self._parse_iso_datetime(value)
        if last_run is None:
            return True
        return self._now() - last_run >= timedelta(hours=hours)

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    @staticmethod
    def _list_state_value(state: dict[str, object], key: str) -> list[str]:
        raw_value = state.get(key, [])
        if not isinstance(raw_value, list):
            return []
        return [str(item).strip() for item in raw_value if str(item).strip()]

    @staticmethod
    def _extract_topics(discovered: list[Any]) -> list[str]:
        topics: list[str] = []
        strategy_map = {
            "search": "相近兴趣",
            "related_chain": "相关推荐",
            "trending": "站内热榜",
            "explore": "跨圈探索",
        }
        for item in discovered:
            tags: Any = (
                item.get("tags", []) if isinstance(item, dict) else getattr(item, "tags", [])
            )
            if isinstance(tags, list):
                for tag in tags:
                    text = str(tag).strip()
                    if text:
                        topics.append(text)
            if isinstance(item, dict):
                source_strategy = str(item.get("source_strategy", "")).strip()
            else:
                source_strategy = str(getattr(item, "source_strategy", "")).strip()
            if source_strategy:
                topics.append(strategy_map.get(source_strategy, source_strategy))
        return topics

    @staticmethod
    def _dedupe_topics(topics: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for topic in topics:
            text = topic.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            ordered.append(text)
        return ordered
