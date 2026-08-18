"""Continuous refresh controller for the local API runtime."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

from openbiliclaw.config import SchedulerConfig
from openbiliclaw.discovery.pool_snapshot import (
    build_cold_start_pool_snapshot,
    build_pool_distribution_snapshot,
)
from openbiliclaw.recommendation.delight import (
    DEFAULT_DELIGHT_THRESHOLD,
    effective_delight_threshold,
)
from openbiliclaw.runtime.image_cache import (
    cleanup_image_cache,
    prefetch_cover,
    select_prefetch_targets,
)
from openbiliclaw.runtime.keyword_fetch import PLATFORM_BILIBILI as _KW_PLATFORM_BILIBILI
from openbiliclaw.runtime.presence import PresenceTracker, background_llm_work_allowed
from openbiliclaw.soul.avoidance_speculator import choose_next_avoidance_candidate
from openbiliclaw.soul.speculator import (
    _normalize_probe_mode,
    build_probe_axis,
    choose_next_probe_candidate,
)
from openbiliclaw.sources.platforms import source_family as _source_family

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from openbiliclaw.runtime.image_fetch import ImageFetchCoordinator
    from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry
    from openbiliclaw.storage.database import PoolMaintenanceResult

logger = logging.getLogger(__name__)

_MAX_DISCOVERY_BACKFILL_PER_REFRESH = 60
_DEFAULT_CANDIDATE_EVAL_BATCH_SIZE = 45
# CALIBRATION PROVENANCE: production pools are normally 300-500 rows. Eight
# batches of 50 can converge one full pool in a tick while committing/releasing
# the writer lock between batches. Larger backlogs continue on the next tick.
_POOL_MAINTENANCE_BATCH_SIZE = 50
_POOL_MAINTENANCE_MAX_BATCHES_PER_TICK = 8
# Even when readiness counts remain stable, time-based stale eligibility and
# same-count topic/source replacement can change maintenance outcomes. A
# 10-minute safety pass bounds that drift while avoiding full ranked scans on
# every 60-second scheduler tick.
_POOL_MAINTENANCE_SAFETY_SCAN_SECONDS = 10 * 60
# Five minutes is the same order of magnitude as the supply cooldown ceiling,
# bounding repeated diagnostic DB work while still guaranteeing at least one
# full diagnostic for each source-exhaustion episode.
_EMPTY_PLAN_DIAG_INTERVAL_SECONDS = 300.0


class InitialPoolUnavailableError(RuntimeError):
    """Initial discovery finished without producing a serviceable pool row."""

    def __init__(self, *, discovered_count: int, pending_count: int = 0) -> None:
        self.discovered_count = max(0, int(discovered_count))
        self.pending_count = max(0, int(pending_count))
        super().__init__(
            "initial discovery produced "
            f"{self.discovered_count} candidate(s), but none became serviceable "
            f"after recommendation-copy generation (pending={self.pending_count})"
        )


_DISCOVERY_REPLENISH_LOW_WATERMARK_RATIO = 0.90
_BILIBILI_EXPENSIVE_DISCOVERY_GAP_RATIO = 0.20
_BILIBILI_EXPENSIVE_DISCOVERY_MIN_GAP = 20
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
    "bilibili": 5,
}
_PLATFORM_SOURCE_ORDER = (
    "bilibili",
    "xiaohongshu",
    "douyin",
    "youtube",
    "twitter",
    "zhihu",
    "reddit",
    "linuxdo",
    "bangumi",
    "v2ex",
    "weibo",
)
_BILIBILI_DISCOVERY_SOURCES = ("search", "related_chain", "trending", "explore")
# Pool-share fairness (spec 2026-07-20, Phase 3): max over-share rows evicted
# per drain tick. Deliberately small so pool composition converges toward the
# configured shares gently (≤3 rows/minute) instead of a disruptive bulk purge;
# admission fills the freed slots with under-share supply the same tick.
_POOL_REBALANCE_MAX_PER_TICK = 3
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


def _producer_supply_progress_count(result: object) -> int:
    """Count concrete progress from one platform producer result.

    Candidate-pipeline producers expose ``enqueued``/``inserted``.  Those
    fields are authoritative when present because ``discovered`` may include
    rows that were all rejected as durable duplicates.  Task-backed producers
    (Bilibili/XHS) also use ``enqueued`` to report newly scheduled browser
    work.  Legacy direct producers fall back to ``cached`` or ``discovered``.
    """

    if not isinstance(result, Mapping):
        return 0
    for key in ("enqueued", "inserted"):
        if key in result:
            try:
                return max(0, int(result.get(key, 0) or 0))
            except (TypeError, ValueError):
                return 0
    for key in ("cached", "discovered"):
        if key in result:
            try:
                return max(0, int(result.get(key, 0) or 0))
            except (TypeError, ValueError):
                return 0
    return 0


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


def _call_accepts_keywords(fn: Any) -> bool:
    """Return whether a discovery callable accepts a ``keywords=`` keyword.

    Used for the direct-engine B站 search fallback path so the unified keyword
    planner's injected words are only forwarded to engines/stubs that declare
    the kwarg — stubs without it stay byte-compatible (flag-off / tests).
    """
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "keywords" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _call_accepts_keyword_ids(fn: Any) -> bool:
    """Return whether a discovery callable accepts a ``keyword_ids=`` keyword.

    P1.8 parallel of :func:`_call_accepts_keywords` for the direct-engine B站
    search fallback so the keyword→id provenance map is only forwarded to
    engines that declare it; stubs without it stay byte-compatible.
    """
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "keyword_ids" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _string_state_map(value: object) -> dict[str, str]:
    """Normalize a JSON object field into a string-to-string map."""
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


class SupportsRuntimeState(Protocol):
    def load_discovery_runtime_state(self) -> dict[str, object]: ...
    def save_discovery_runtime_state(self, state: dict[str, object]) -> None: ...
    def update_discovery_runtime_state(
        self,
        mutator: Callable[[dict[str, object]], dict[str, object] | None],
    ) -> dict[str, object]: ...
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
    def mark_pool_purged_by_reinit(self) -> int: ...
    def count_pool_readiness(self, *, xhs_self_nickname: str = "") -> dict[str, int]: ...
    def count_pool_candidates_by_source(self) -> dict[str, int]: ...
    def count_pool_available_candidates_by_source(
        self, *, max_per_topic_group: int = 3, xhs_self_nickname: str = ""
    ) -> dict[str, int]: ...
    def count_pool_raw_material_candidates(self) -> int: ...
    def count_pool_raw_material_by_source(self) -> dict[str, int]: ...
    def get_pool_distribution_counts(self) -> dict[str, dict[str, int]]: ...
    def maintain_pool_inventory(
        self,
        *,
        target: int,
        raw_ceiling: int,
        source_share_quotas: dict[str, int],
        raw_source_share_quotas: dict[str, int] | None = None,
        max_per_topic_group: int = 3,
        max_per_explore_cluster: int = 3,
        stale_max_age_days: int = 14,
        xhs_self_nickname: str = "",
        recover_suppressed: bool = True,
    ) -> PoolMaintenanceResult: ...
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
    def mark_delight_seen(self, bvid: str) -> bool: ...
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

    async def drain_pending_expression_copy(
        self,
        *,
        profile: Any,
        limit: int,
    ) -> int: ...

    async def precompute_delight_scores(self, *, profile: Any, limit: int) -> int: ...

    async def classify_pool_backlog(self, *, profile: Any, limit: int) -> int: ...

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
    image_fetch_coordinator: ImageFetchCoordinator | None = None
    discovery_candidate_pipeline: Any | None = None
    candidate_eval_coordinator: Any | None = None
    expression_copy_coordinator: Any | None = None
    # OpenClaw's bridge is intentionally one-shot: it has no daemon loop to
    # own ExpressionCopyCoordinator.  When supplied, this callback finishes
    # the durable copy stage synchronously after inline admission instead of
    # scheduling the daemon-oriented precompute path.
    one_shot_expression_copy_callback: Callable[[Any], Any] | None = None
    # Non-daemon bridges can cap their first source/evaluation wave so an
    # interactive request produces a serviceable batch before its own timeout.
    # ``0`` preserves the existing uncapped inline behavior; API runtime
    # controllers leave this at zero and use CandidateEvalCoordinator instead.
    one_shot_inline_eval_limit: int = 0
    llm_concurrency_gate: Any | None = None
    bilibili_producer: Any | None = None
    xhs_producer: Any | None = None
    douyin_producer: Any | None = None
    youtube_producer: Any | None = None
    x_producer: Any | None = None
    zhihu_producer: Any | None = None
    reddit_producer: Any | None = None
    bangumi_producer: Any | None = None
    linuxdo_producer: Any | None = None
    v2ex_producer: Any | None = None
    weibo_producer: Any | None = None
    scheduler_config: Any = field(default_factory=SchedulerConfig)
    presence: PresenceTracker = field(default_factory=PresenceTracker)
    # gui-init D1: optional init-aware gate. When it returns True (a guided init
    # is active) ALL background loops pause so they don't race init's explicit
    # analyze/build. ``run_init_backfill`` bypasses this (it never calls
    # ``_llm_work_allowed``), so init's own discovery is not self-blocked.
    init_active_check: Callable[[], bool] | None = None
    signal_event_threshold: int = 6
    event_refresh_minutes: int = 0
    trending_refresh_minutes: int = 3
    explore_refresh_minutes: int = 3
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
    # P1.6: unified keyword planner (deficit-pulled merged keyword generation).
    # Constructed as its own object in ``api/runtime_context.py`` because the
    # controller holds no ``llm_service``. Its loop is launched by
    # ``run_forever``; with the feature flag off (default) the loop is a pure
    # no-op, so wiring it in is zero behavior change. ``None`` (the default,
    # used by tests that build the controller directly) means the planner loop
    # returns immediately.
    keyword_planner: Any | None = None
    # P1.7: unified keyword planner FETCH coordinator. Drives the B站 search
    # inline-admit lifecycle (claim → inject as ``queries`` → used / failed) when
    # the flag is on. Constructed in ``api/runtime_context.py``; ``None`` (tests
    # / flag off) → the B站 search keeps its legacy self-generating path.
    keyword_fetch: Any | None = None
    # Extension-online account bootstrap refresh.  This loop has its own
    # direct presence/profile/init gates and must not inherit the LLM gate.
    source_incremental_sync: Any | None = None
    _manual_refresh_task: asyncio.Task[None] | None = None
    _discovery_drain_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )
    # Periodic producer loops and demand-driven candidate supply can wake the
    # same platform at the same instant.  Keep one lock per source so duplicate
    # same-source fetches are skipped without serializing unrelated platforms.
    _producer_tick_locks: dict[str, asyncio.Lock] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _candidate_supply_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )
    # v0.3.62+ global "skip-if-busy" gate. Direct refresh execution is
    # intentionally centralized: periodic ticks call ``refresh_if_needed``;
    # user/manual replenishment calls ``force_refresh``. Event/feedback/init
    # paths only queue a reason and wait for the unified scheduler.
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
    _pending_replenishment_reasons: set[str] = field(default_factory=set, init=False)
    # A cheap readiness fingerprint skips repeated ranked maintenance scans.
    # Forced/post-refresh paths bypass it, and a periodic safety pass still
    # catches stale-time or same-count composition changes.
    _last_pool_maintenance_fingerprint: tuple[int, int, int, int, int] | None = field(
        default=None,
        init=False,
    )
    _last_pool_maintenance_scan_at: datetime | None = field(default=None, init=False)
    _last_empty_plan_diag_at: datetime | None = field(default=None, init=False)
    _last_empty_plan_fingerprint: tuple[int] | None = field(default=None, init=False)
    # Peak-hour deferral: cached servable-pool count so refill loops can pass
    # ``pool_available`` to the LLM gate without re-reading the DB every tick.
    # Invalidated after a short TTL; refresh loops re-read on demand.
    _peak_pool_available: int = field(default=0, init=False)
    _peak_pool_available_at: float = field(default=0.0, init=False)
    _suppressed_empty_plan_count: int = field(default=0, init=False)
    # Pool-share fairness (spec 2026-07-20, Phase 4): last per-family
    # (available, target, deficit) snapshot. The per-source deficit summary is
    # only logged when this changes, so a steady state stays quiet but any
    # shift (e.g. an under-share source recovering) is visible in one line.
    _last_source_deficit_snapshot: dict[str, tuple[int, int, int]] = field(
        default_factory=dict,
        init=False,
    )
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
    _startup_maintenance_completed: bool = field(default=False, init=False)
    _last_pool_maintenance_succeeded: bool = field(default=False, init=False)

    _signal_event_types = [
        "view",
        "search",
        "favorite",
        "like",
        "coin",
        "comment",
        "feedback",
    ]

    def _llm_work_allowed(self, *, pool_available: int | None = None) -> bool:
        """Return whether daemon-owned background LLM / embedding work can run.

        ``pool_available`` marks the caller as refill traffic and carries the
        current servable-pool count, so peak-hour deferral can keep an
        emergency floor. ``None`` means non-refill (maintenance / push).
        """
        # Pause every background loop while a guided init is active (gui-init
        # D1) — the continuous refresh / soul-pipeline / producer ticks all gate
        # on this, so init's explicit analyze/build/backfill runs uncontended.
        if self.init_active_check is not None:
            try:
                if self.init_active_check():
                    return False
            except Exception:
                pass
        allowed = background_llm_work_allowed(
            self.scheduler_config,
            self.presence,
            pool_available=pool_available,
        )
        if allowed != self._last_llm_gate_allowed:
            logger.info(
                "Background LLM work gate %s",
                "allowed" if allowed else "blocked",
            )
            self._last_llm_gate_allowed = allowed
        return allowed

    def _peak_refill_pool_available(self) -> int:
        """Return a cached servable-pool count for refill gate admission.

        Only meaningful when peak-hour deferral is enabled; the short TTL
        keeps the per-loop gate check free of a DB read on every tick.
        """
        import time as _time

        now = _time.monotonic()
        if self._peak_pool_available_at and now - self._peak_pool_available_at < 30:
            return self._peak_pool_available
        try:
            counts = self._pool_readiness_counts()
            available = int(counts.get("available", 0) or 0)
        except Exception:
            logger.debug("peak pool available read failed", exc_info=True)
            available = 0
        self._peak_pool_available = available
        self._peak_pool_available_at = now
        return available

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
            counts = self._normalize_pool_readiness(readiness)
        except Exception:
            available = int(self.database.count_pool_candidates(xhs_self_nickname=nickname))
            counts = {
                "available": max(0, available),
                "raw": max(0, available),
                "pending": 0,
                "admitted_pending_copy": 0,
                "admitted_pending_available": 0,
                "pending_eval": 0,
                "evaluated_pending": 0,
            }
        self._update_llm_inventory_state(counts["available"])
        return counts

    @staticmethod
    def _normalize_pool_readiness(readiness: Mapping[str, int]) -> dict[str, int]:
        """Normalize durable readiness fields for runtime/API payloads."""
        available = int(readiness.get("available", 0))
        return {
            "available": max(0, available),
            "raw": max(0, int(readiness.get("raw", available))),
            "pending": max(0, int(readiness.get("pending", 0))),
            "admitted_pending_copy": max(
                0,
                int(readiness.get("admitted_pending_copy", 0)),
            ),
            "admitted_pending_available": max(
                0,
                int(readiness.get("admitted_pending_available", 0)),
            ),
            "pending_eval": max(0, int(readiness.get("pending_eval", 0))),
            "evaluated_pending": max(0, int(readiness.get("evaluated_pending", 0))),
        }

    async def _pool_readiness_counts_async(self) -> dict[str, int]:
        """Read readiness off-loop when the production DB worker is available."""
        try:
            readiness = await self._read_isolated_pool_readiness()
        except Exception:
            logger.debug("isolated runtime pool status read failed", exc_info=True)
            readiness = None
        if readiness is None:
            return await asyncio.to_thread(self._pool_readiness_counts)
        counts = self._normalize_pool_readiness(readiness)
        self._update_llm_inventory_state(counts["available"])
        return counts

    def _update_llm_inventory_state(self, available: int) -> None:
        """Synchronize refill admission from canonical durable availability."""
        gate = self.llm_concurrency_gate
        update = getattr(gate, "update_inventory", None)
        if callable(update):
            update(available=max(0, int(available)), target=self.pool_target_count)

    @staticmethod
    def _pool_count_payload(counts: dict[str, int]) -> dict[str, int]:
        return {
            "pool_available_count": int(counts.get("available", 0)),
            "pool_raw_count": int(counts.get("raw", counts.get("available", 0))),
            "pool_pending_count": int(counts.get("pending", 0)),
            "pool_pending_eval_count": int(counts.get("pending_eval", 0)),
            "pool_evaluated_pending_count": int(counts.get("evaluated_pending", 0)),
        }

    def _profile_delight_default_threshold(self) -> float:
        exploration_openness = 0.5
        with suppress(Exception):
            preference_layer = self.memory_manager.get_layer("preference")
            preference_data = getattr(preference_layer, "data", {})
            if isinstance(preference_data, dict):
                exploration_openness = float(preference_data.get("exploration_openness", 0.5))
        return effective_delight_threshold(exploration_openness)

    def _dynamic_delight_threshold(self) -> float:
        default_threshold = self._profile_delight_default_threshold()
        threshold_fn = getattr(self.database, "dynamic_delight_threshold", None)
        if callable(threshold_fn):
            with suppress(Exception):
                return float(threshold_fn(default_threshold=default_threshold))
        return default_threshold

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
                min_delight_score=self._dynamic_delight_threshold(),
            )
        pool_counts = self._pool_readiness_counts()
        payload: dict[str, object] = {
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
        status_payload = getattr(self.candidate_eval_coordinator, "status_payload", None)
        if callable(status_payload):
            with suppress(Exception):
                payload.update(status_payload())
        expression_status = getattr(self.expression_copy_coordinator, "status_payload", None)
        if callable(expression_status):
            with suppress(Exception):
                payload.update(expression_status())
        gate_status_payload = getattr(self.llm_concurrency_gate, "status_payload", None)
        if callable(gate_status_payload):
            with suppress(Exception):
                payload.update(gate_status_payload())
        return payload

    async def refresh_if_needed(self) -> dict[str, object]:
        """Refresh discovery candidates when thresholds are met.

        Runtime replenishment now has one deciding path: the periodic scheduler
        calls this method, while event / feedback / init hooks only queue a
        reason through ``request_replenishment``. A module-level
        ``_refresh_lock`` (an ``asyncio.Lock``) is checked at the very top: if
        another refresh is already in progress, this call returns
        ``{"skipped": True, "reason": "another refresh holds lock"}``
        immediately rather than queueing. The remaining body runs inside
        ``async with self._refresh_lock:``, so the lock is released even on
        exception paths.

        Internal helpers (``_run_refresh_plan``, ``force_refresh``)
        intentionally do NOT acquire this lock — only the public
        ``refresh_if_needed`` entry does, so callers reaching it from
        different paths can't double-acquire.
        """
        if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
            return {"refreshed": False, "strategies": [], "reason": "llm_paused"}

        if self._refresh_lock.locked():
            logger.debug("refresh_if_needed skipped: another refresh in flight")
            return {"skipped": True, "reason": "another refresh holds lock"}

        async with self._refresh_lock:
            state = self.memory_manager.load_discovery_runtime_state()
            queued_reasons = self._consume_replenishment_reasons()

            def _result(payload: dict[str, object]) -> dict[str, object]:
                if queued_reasons:
                    payload["queued_reasons"] = queued_reasons
                return payload

            if not self._is_initialized():
                return _result({"refreshed": False, "strategies": [], "reason": "not_initialized"})

            pool_at_cap = await self._enforce_pool_cap_async()
            await self._publish_pool_status_if_changed()
            if pool_at_cap:
                return _result({"refreshed": False, "strategies": [], "reason": "pool_at_cap"})

            profile = await self.soul_engine.get_profile()
            plan = self._build_refresh_plan(state)
            if not plan:
                return _result({"refreshed": False, "strategies": [], "reason": "below_threshold"})

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
        progress_callback: Callable[[int, int, str], Awaitable[None] | None] | None = None,
    ) -> int:
        """Backfill the initial discovery pool for guided init.

        Holds ``_refresh_lock`` so it serializes with continuous refresh and
        never races it on ``content_cache`` (gui-init spec §5d). Mirrors the
        CLI's staged ``_INIT_DISCOVERY_PLAN`` backfill, but against this
        controller's live ``discovery_engine``/``database``. Cooperative
        cancel: ``async with`` releases the lock on ``CancelledError``.
        Discovery alone is not a completion boundary: rows also need
        classification and recommendation copy before ``serve()`` can return
        them.  This method therefore holds the refresh lock through the first
        synchronous expression-copy drain and only succeeds once at least one
        canonical pool row is serviceable. Returns the total number of items
        discovered.

        Force re-init purges the old pool via
        ``run_guided_init(purge_pool_callback=...)`` at stage-4 start, before
        this backfill is invoked — the purge is the pipeline's job so a
        backfill implementation cannot silently skip it.
        """

        async def _report(done: int, total: int, note: str) -> None:
            if progress_callback is None:
                return
            result = progress_callback(done, total, note)
            if inspect.isawaitable(result):
                await result

        target = max(0, int(target_pool_count))
        if target == 0:
            await _report(4, 4, "已跳过首轮内容池构建")
            return 0

        discovered_count = 0
        async with self._refresh_lock:
            copy_error: BaseException | None = None
            for strategies in _INIT_DISCOVERY_PLAN:
                current = self.database.count_pool_candidates()
                self._update_llm_inventory_state(current)
                if current >= target:
                    break
                await _report(0, 4, "正在基于完整画像生成发现方向并抓取候选")
                request_limit = max(20, target - current)
                pool_snapshot = self._build_init_pool_snapshot(
                    profile,
                    current_pool_count=current,
                    target_pool_count=target,
                )
                discovered = await self.discovery_engine.discover(
                    profile,
                    strategies=strategies,
                    limit=request_limit,
                    fully_parallel=fully_parallel,
                    pool_snapshot=pool_snapshot,
                )
                discovered_count += len(discovered)
                current = self.database.count_pool_candidates()
                self._update_llm_inventory_state(current)
                await _report(
                    2,
                    4,
                    f"已发现 {discovered_count} 条候选，正在生成首轮推荐文案",
                )

                if current < target:
                    try:
                        copied = await self.recommendation_engine.drain_pending_expression_copy(
                            profile=profile,
                            limit=max(1, target),
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        copy_error = exc
                        copied = max(0, int(getattr(exc, "completed", 0) or 0))
                    current = self.database.count_pool_candidates()
                    self._update_llm_inventory_state(current)
                    await _report(
                        3,
                        4,
                        f"已生成 {copied} 条推荐文案，正在验证首轮内容可用性",
                    )

                if current >= target:
                    break

            available = self.database.count_pool_candidates()
            self._update_llm_inventory_state(available)
            if available <= 0:
                pending = 0
                count_readiness = getattr(self.database, "count_pool_readiness", None)
                if callable(count_readiness):
                    with suppress(Exception):
                        readiness = count_readiness(xhs_self_nickname=self._xhs_self_nickname())
                        if isinstance(readiness, dict):
                            pending = int(readiness.get("pending", 0) or 0)
                if copy_error is not None:
                    logger.warning(
                        "guided-init expression copy failed before first pool row was ready: %s",
                        copy_error,
                    )
                raise InitialPoolUnavailableError(
                    discovered_count=discovered_count,
                    pending_count=pending,
                ) from copy_error

            if copy_error is not None:
                logger.warning(
                    "guided-init expression copy partially failed after %d row(s) became ready: %s",
                    available,
                    copy_error,
                )
            await _report(4, 4, f"首轮内容池已就绪（{available} 条可直接浏览）")
        return discovered_count

    def _build_init_pool_snapshot(
        self,
        profile: Any,
        *,
        current_pool_count: int,
        target_pool_count: int,
    ) -> Any | None:
        if current_pool_count <= 0:
            return build_cold_start_pool_snapshot(
                profile,
                pool_target_count=target_pool_count,
                source_targets=self._source_target_counts(total=target_pool_count),
            )
        try:
            return build_pool_distribution_snapshot(
                self.database,
                pool_target_count=target_pool_count,
                source_targets=self._source_target_counts(total=target_pool_count),
            )
        except Exception:
            logger.debug("init backfill pool snapshot unavailable", exc_info=True)
            return None

    async def force_refresh(self) -> dict[str, object]:
        """Run a full refresh immediately, bypassing runtime thresholds.

        Runs all 4 Bilibili strategies in a single discover() call so they
        execute concurrently via asyncio.gather, maximizing pool diversity. The pool
        target still applies as a hard cap — if the pool is already full, no
        discovery runs and overflow is trimmed.

        v0.3.62+: also acquires ``_refresh_lock`` so manual refresh
        (which calls ``force_refresh`` rather than ``refresh_if_needed``)
        respects the global skip-if-busy gate. Without this, periodic
        + manual / pool-low refresh used to run through different code paths,
        amplifying Bilibili API load and SQLite write contention.
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
        queued_reasons = self._consume_replenishment_reasons()

        def _result(payload: dict[str, object]) -> dict[str, object]:
            if queued_reasons:
                payload["queued_reasons"] = queued_reasons
            return payload

        if not self._is_initialized():
            return _result({"refreshed": False, "strategies": [], "reason": "not_initialized"})

        pool_at_cap = await self._enforce_pool_cap_async(force_scan=True)
        await self._publish_pool_status_if_changed()
        if pool_at_cap:
            return _result({"refreshed": False, "strategies": [], "reason": "pool_at_cap"})

        profile = await self.soul_engine.get_profile()
        plan = self._build_source_replenishment_plan()
        if not plan:
            return _result({"refreshed": False, "strategies": [], "reason": "below_threshold"})
        refresh_result = await self._run_refresh_plan(
            state=state,
            profile=profile,
            plan=plan,
            reason="manual",
        )
        return _result(refresh_result)

    def _enforce_pool_cap(self) -> bool:
        """Run pool maintenance and report whether frontend availability is at target.

        ``pool_target_count`` is a frontend-visible availability floor, not the
        raw material cap. Raw rows may exceed it until ``_raw_material_ceiling``.
        """
        self._last_pool_maintenance_succeeded = False
        kwargs = self._pool_maintenance_kwargs()
        bounded = callable(getattr(self.database, "maintain_pool_inventory_async", None))
        last_at_target = False
        try:
            for _batch_index in range(_POOL_MAINTENANCE_MAX_BATCHES_PER_TICK if bounded else 1):
                call_kwargs = dict(kwargs)
                if bounded:
                    call_kwargs["max_mutations"] = _POOL_MAINTENANCE_BATCH_SIZE
                maintain = cast("Any", self.database.maintain_pool_inventory)
                result = maintain(**call_kwargs)
                last_at_target = self._record_pool_maintenance_result(result)
                if not bool(getattr(result, "has_more", False)):
                    break
            return last_at_target
        except Exception:
            logger.exception("bounded pool maintenance failed")
            pool_available = self.database.count_pool_candidates(
                xhs_self_nickname=self._xhs_self_nickname()
            )
            self._update_llm_inventory_state(pool_available)
            return pool_available >= self.pool_target_count

    def _pool_maintenance_kwargs(self) -> dict[str, object]:
        """Build the canonical arguments shared by sync and async runners."""
        return {
            "target": self.pool_target_count,
            "raw_ceiling": self._raw_material_ceiling(),
            "source_share_quotas": self._source_target_counts(),
            "raw_source_share_quotas": self._raw_source_target_counts(),
            "max_per_topic_group": max(3, self.pool_target_count // 10),
            "max_per_explore_cluster": 3,
            "stale_max_age_days": 14,
            "xhs_self_nickname": self._xhs_self_nickname(),
        }

    def _record_pool_maintenance_result(self, result: PoolMaintenanceResult) -> bool:
        """Publish one batch's metrics and update the in-memory inventory gate."""
        log_fn = logger.error if result.rolled_back else logger.info
        log_fn(
            "pool_maintenance available=%s->%s target=%s raw=%s->%s/%s "
            "mutations=%s has_more=%s lock_wait_ms=%.1f total_ms=%.1f "
            "rolled_back=%s reason=%s",
            result.available_before,
            result.available_after,
            result.target,
            result.raw_before,
            result.raw_after,
            result.raw_ceiling,
            getattr(result, "mutation_count", 0),
            getattr(result, "has_more", False),
            float(getattr(result, "lock_wait_ms", 0.0)),
            float(getattr(result, "total_ms", 0.0)),
            result.rolled_back,
            result.reason,
        )
        logger.debug(
            "pool_maintenance_detail protected_available=%s recovered_suppressed=%s "
            "trimmed_stale=%s trimmed_explore_cluster=%s trimmed_ready_reserve=%s "
            "trimmed_evaluated=%s trimmed_raw=%s trimmed_by_source=%s "
            "deferred_topic_trim=%s deferred_source_trim=%s deferred_stale_trim=%s "
            "deferred_explore_cluster_trim=%s untrimmed_raw_excess=%s "
            "recovery_ms=%.1f stale_ms=%.1f explore_ms=%.1f topic_ms=%.1f "
            "source_ms=%.1f raw_ms=%.1f write_ms=%.1f",
            result.protected_available,
            result.recovered_suppressed,
            result.trimmed_stale,
            result.trimmed_explore_cluster,
            result.trimmed_ready_reserve,
            result.trimmed_evaluated,
            result.trimmed_raw,
            result.trimmed_by_source,
            result.deferred_topic_trim,
            result.deferred_source_trim,
            result.deferred_stale_trim,
            result.deferred_explore_cluster_trim,
            result.untrimmed_raw_excess,
            float(getattr(result, "recovery_ms", 0.0)),
            float(getattr(result, "stale_trim_ms", 0.0)),
            float(getattr(result, "explore_trim_ms", 0.0)),
            float(getattr(result, "topic_trim_ms", 0.0)),
            float(getattr(result, "source_trim_ms", 0.0)),
            float(getattr(result, "raw_trim_ms", 0.0)),
            float(getattr(result, "write_ms", 0.0)),
        )
        if result.rolled_back:
            self._update_llm_inventory_state(result.available_before)
            return result.available_before >= self.pool_target_count
        self._last_pool_maintenance_succeeded = True
        self._update_llm_inventory_state(result.available_after)
        if int(getattr(result, "mutation_count", 0) or 0) > 0:
            self.notify_expression_copy_pending("pool_maintenance")
        return result.at_target

    @staticmethod
    def _pool_maintenance_fingerprint(
        counts: dict[str, int],
    ) -> tuple[int, int, int, int, int]:
        """Return the stable inventory fields that gate heavy maintenance."""
        return (
            max(0, int(counts.get("available", 0))),
            max(0, int(counts.get("raw", 0))),
            max(0, int(counts.get("pending", 0))),
            max(0, int(counts.get("pending_eval", 0))),
            max(0, int(counts.get("evaluated_pending", 0))),
        )

    async def _read_isolated_pool_readiness(self) -> dict[str, int] | None:
        """Read readiness on the serve worker when the database supports it."""
        reader = getattr(self.database, "count_pool_readiness_isolated_async", None)
        if not callable(reader):
            return None
        counts = await reader(xhs_self_nickname=self._xhs_self_nickname())
        return {str(key): max(0, int(value)) for key, value in counts.items()}

    async def _enforce_pool_cap_async(self, *, force_scan: bool = False) -> bool:
        """Run SQLite-heavy pool maintenance without blocking the API loop.

        Production uses the database-owned, single-thread maintenance worker.
        Each call mutates at most 50 rows, commits, and returns ``has_more``;
        this coroutine explicitly yields between batches. The interactive
        serve worker is separate, so a blocked maintenance batch cannot queue a
        recommendation read behind it.
        """
        runner = getattr(self.database, "maintain_pool_inventory_async", None)
        if not callable(runner):
            # Test doubles and legacy adapters retain the old off-loop bridge.
            return await asyncio.to_thread(self._enforce_pool_cap)

        self._last_pool_maintenance_succeeded = False
        if not force_scan:
            try:
                pre_counts = await self._read_isolated_pool_readiness()
            except Exception:
                logger.debug("pool maintenance fingerprint read failed", exc_info=True)
                pre_counts = None
            if pre_counts is not None:
                fingerprint = self._pool_maintenance_fingerprint(pre_counts)
                last_scan = self._last_pool_maintenance_scan_at
                safety_due = (
                    last_scan is None
                    or (self._now() - last_scan).total_seconds()
                    >= _POOL_MAINTENANCE_SAFETY_SCAN_SECONDS
                )
                if fingerprint == self._last_pool_maintenance_fingerprint and not safety_due:
                    assert last_scan is not None
                    pool_available = fingerprint[0]
                    self._last_pool_maintenance_succeeded = True
                    self._update_llm_inventory_state(pool_available)
                    logger.debug(
                        "pool maintenance skipped unchanged fingerprint=%s safety_age_s=%.1f",
                        fingerprint,
                        (self._now() - last_scan).total_seconds(),
                    )
                    return pool_available >= self.pool_target_count

        kwargs = self._pool_maintenance_kwargs()
        last_at_target = False
        has_more = False
        for _batch_index in range(_POOL_MAINTENANCE_MAX_BATCHES_PER_TICK):
            try:
                result = await runner(
                    **kwargs,
                    max_mutations=_POOL_MAINTENANCE_BATCH_SIZE,
                )
            except Exception as exc:
                if "Deferred" in type(exc).__name__ or "writer busy" in str(exc).lower():
                    logger.info("pool maintenance deferred: %s", exc)
                else:
                    logger.exception("bounded pool maintenance worker failed")
                readiness_reader = getattr(
                    self.database,
                    "count_pool_readiness_isolated_async",
                    None,
                )
                if callable(readiness_reader):
                    counts = await readiness_reader(xhs_self_nickname=self._xhs_self_nickname())
                    pool_available = max(0, int(counts.get("available", 0)))
                else:
                    pool_available = await asyncio.to_thread(
                        self.database.count_pool_candidates,
                        xhs_self_nickname=self._xhs_self_nickname(),
                    )
                self._update_llm_inventory_state(pool_available)
                return pool_available >= self.pool_target_count

            last_at_target = self._record_pool_maintenance_result(result)
            has_more = bool(getattr(result, "has_more", False))
            if not has_more:
                break
            await asyncio.sleep(0)
        if has_more:
            self._last_pool_maintenance_fingerprint = None
        elif self._last_pool_maintenance_succeeded:
            try:
                after_counts = await self._read_isolated_pool_readiness()
            except Exception:
                logger.debug("post-maintenance fingerprint read failed", exc_info=True)
                after_counts = None
            if after_counts is not None:
                self._last_pool_maintenance_fingerprint = self._pool_maintenance_fingerprint(
                    after_counts
                )
                self._last_pool_maintenance_scan_at = self._now()
        return last_at_target

    def run_startup_maintenance(self) -> None:
        """Run the host's pre-service pool repair at most once per controller."""
        if self._startup_maintenance_completed:
            return
        try:
            self._enforce_pool_cap()
        except Exception:
            return
        if not self._last_pool_maintenance_succeeded:
            return
        self._startup_maintenance_completed = True

    async def trigger_manual_refresh(self, *, reason: str = "manual") -> dict[str, object]:
        """Schedule one background manual refresh without blocking the caller."""
        normalized_reason = self._normalize_replenishment_reason(reason)
        if not self._is_initialized():
            return {"accepted": False, "state": "idle", "reason": "not_initialized"}
        if self._manual_refresh_task is not None and not self._manual_refresh_task.done():
            return {"accepted": True, "state": "running", "reason": "already_running"}

        self._manual_refresh_state = "running"
        self._manual_refresh_message = "正在补货…"
        self._manual_refresh_started_at = self._now().isoformat()
        self._manual_refresh_finished_at = ""
        logger.info("Manual replenishment requested: reason=%s", normalized_reason)
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

    def _update_discovery_runtime_state(
        self,
        mutator: Callable[[dict[str, object]], dict[str, object] | None],
    ) -> dict[str, object]:
        update_state = getattr(self.memory_manager, "update_discovery_runtime_state", None)
        if callable(update_state):
            return cast("dict[str, object]", update_state(mutator))
        state = self.memory_manager.load_discovery_runtime_state()
        result = mutator(state)
        next_state = state if result is None else result
        self.memory_manager.save_discovery_runtime_state(next_state)
        return next_state

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
        from openbiliclaw.recommendation.exclusion import filter_recommendation_rows

        disliked_phrases = self._load_disliked_topic_phrases()
        if not filter_recommendation_rows(
            [candidate],
            disliked_phrases,
            restore_on_total_fuzzy_match=False,
        ):
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
        now = self._now().isoformat()
        self._update_discovery_runtime_state(
            lambda state: state.update({"last_notification_at": now})
        )

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
            min_delight_score=self._dynamic_delight_threshold(),
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
            "item_key": str(candidate.get("item_key", "")),
            "content_id": str(candidate.get("content_id", "") or candidate.get("bvid", "")),
            "title": str(candidate.get("title", "")),
            "delight_reason": str(candidate.get("delight_reason", "")),
            "delight_score": float(candidate.get("delight_score", 0.0) or 0.0),
            "delight_hook": str(candidate.get("delight_hook", "")),
            "cover_url": str(candidate.get("cover_url", "")),
            "content_url": str(candidate.get("content_url", "")),
            "source_platform": str(candidate.get("source_platform", "") or "bilibili"),
            "published_at": str(candidate.get("published_at", "") or ""),
            "published_label": str(candidate.get("published_label", "") or ""),
            "content_type": str(candidate.get("content_type", "") or "video"),
            "body_text": str(candidate.get("body_text", "") or ""),
            "view_count": int(candidate.get("view_count", 0) or 0),
            "like_count": int(candidate.get("like_count", 0) or 0),
            "comment_count": int(candidate.get("comment_count", 0) or 0),
            "share_count": int(candidate.get("share_count", 0) or 0),
            "danmaku_count": int(candidate.get("danmaku_count", 0) or 0),
            "favorite_count": int(
                candidate.get("favorite_count", 0) or candidate.get("collect_count", 0) or 0
            ),
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
                logger.warning(
                    "effective dislike read failed; falling back to flat preference",
                    exc_info=True,
                )
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
        self.notify_expression_copy_pending("delight_consumed")
        now = self._now().isoformat()
        self._update_discovery_runtime_state(
            lambda state: state.update({"last_delight_notification_at": now})
        )

    def mark_delight_seen(self, bvid: str) -> None:
        """Permanently consume a delight as already handled by the user."""
        marker = getattr(self.database, "mark_delight_seen", None)
        if callable(marker):
            marker(bvid)
        else:
            self.database.mark_delight_notified(bvid)
        self.notify_expression_copy_pending("delight_seen")
        now = self._now().isoformat()
        self._update_discovery_runtime_state(
            lambda state: state.update({"last_delight_notification_at": now})
        )

    def notify_expression_copy_pending(self, reason: str) -> None:
        """Wake the runtime-owned copy coordinator without doing inline LLM work."""

        notify = getattr(self.expression_copy_coordinator, "notify", None)
        if not callable(notify):
            return
        try:
            notify(str(reason))
        except Exception:
            logger.warning("expression-copy runtime notification failed", exc_info=True)

    async def prepare_delight_candidates(self) -> int:
        """Warm ready-to-push delight candidates even when no refresh runs."""
        if not self._is_initialized():
            return 0
        profile = await self.soul_engine.get_profile()
        delight = getattr(self.recommendation_engine, "precompute_delight_scores", None)
        if callable(delight):
            return int(await delight(profile=profile, limit=30))
        return int(await self.recommendation_engine.precompute_pool_copy(profile=profile, limit=0))

    @staticmethod
    def _normalize_replenishment_reason(reason: str) -> str:
        normalized = str(reason or "").strip().lower().replace("-", "_").replace(" ", "_")
        return normalized or "unknown"

    def _queue_replenishment_reason(self, reason: str) -> dict[str, object]:
        normalized = self._normalize_replenishment_reason(reason)
        self._pending_replenishment_reasons.add(normalized)
        return {
            "refreshed": False,
            "strategies": [],
            "reason": "queued",
            "queued_reason": normalized,
        }

    def _consume_replenishment_reasons(self) -> list[str]:
        reasons = sorted(self._pending_replenishment_reasons)
        self._pending_replenishment_reasons.clear()
        return reasons

    async def request_replenishment(
        self,
        *,
        reason: str,
        force: bool = False,
    ) -> dict[str, object]:
        """Single public ingress for replenishment requests.

        Non-force requests only record why the next scheduler pass should
        re-check the pool. Force requests are reserved for explicit user actions
        or UI paths that just consumed the visible pool.
        """
        normalized = self._normalize_replenishment_reason(reason)
        if normalized == "feedback":
            self.notify_expression_copy_pending("feedback")
        if force:
            return await self.trigger_manual_refresh(reason=normalized)
        queued = self._queue_replenishment_reason(normalized)
        return {
            "accepted": True,
            "state": "queued",
            "reason": normalized,
            "refresh": queued,
        }

    async def supply_candidates_once(self, *, reason: str) -> dict[str, object]:
        """Run one demand-driven, quota-aware candidate supply wave.

        The candidate evaluator calls this when it has no raw work while the
        visible pool is below target.  Wake every configured platform producer
        with its own source deficit, then run the established Bilibili refresh
        plan.  The returned productivity contract is based on durable inserts
        or newly queued source work, never on the fact that a strategy merely
        executed.
        """

        if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
            return {
                "refreshed": False,
                "strategies": [],
                "reason": "llm_paused",
                "supply_progress_count": 0,
                "supply_productive": False,
            }
        if self._candidate_supply_lock.locked():
            return {
                "refreshed": False,
                "strategies": [],
                "reason": "candidate_supply_in_flight",
                "supply_progress_count": 0,
                "supply_productive": False,
            }

        async with self._candidate_supply_lock:
            await self.request_replenishment(reason=reason)
            producer_results = await self._run_deficit_producers_once()
            producer_progress = sum(
                _producer_supply_progress_count(result) for result in producer_results.values()
            )
            try:
                refresh_result = await self.refresh_if_needed()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if producer_progress <= 0:
                    raise
                logger.warning(
                    "candidate supply Bilibili refresh failed after other sources "
                    "made progress: %s",
                    exc,
                )
                refresh_result = {
                    "refreshed": False,
                    "strategies": [],
                    "reason": "refresh_failed_after_producer_progress",
                    "refresh_error": str(exc),
                }

            raw_refresh_progress = refresh_result.get("supply_inserted_count", 0)
            if isinstance(raw_refresh_progress, int | float | str):
                try:
                    refresh_progress = max(0, int(raw_refresh_progress or 0))
                except ValueError:
                    refresh_progress = 0
            else:
                refresh_progress = 0
            supply_progress = producer_progress + refresh_progress
            return {
                **refresh_result,
                "producer_results": producer_results,
                "supply_progress_count": supply_progress,
                "supply_productive": supply_progress > 0,
            }

    async def _run_deficit_producers_once(self) -> dict[str, dict[str, object]]:
        """Wake all platform producers once and retain per-source diagnostics."""

        tickers = {
            "bilibili": self._tick_bilibili_producer,
            "xiaohongshu": self._tick_xhs_producer,
            "douyin": self._tick_douyin_producer,
            "youtube": self._tick_youtube_producer,
            "twitter": self._tick_x_producer,
            "zhihu": self._tick_zhihu_producer,
            "reddit": self._tick_reddit_producer,
            "bangumi": self._tick_bangumi_producer,
            "linuxdo": self._tick_linuxdo_producer,
            "v2ex": self._tick_v2ex_producer,
            "weibo": self._tick_weibo_producer,
        }
        raw_results = await asyncio.gather(
            *(ticker() for ticker in tickers.values()),
            return_exceptions=True,
        )
        results: dict[str, dict[str, object]] = {}
        for (source, _ticker), raw_result in zip(
            tickers.items(),
            raw_results,
            strict=True,
        ):
            if isinstance(raw_result, asyncio.CancelledError):
                raise raw_result
            if isinstance(raw_result, BaseException):
                logger.warning(
                    "candidate supply producer failed: source=%s error=%s",
                    source,
                    raw_result,
                )
                results[source] = {
                    "source_family": source,
                    "reason": "error",
                    "error": str(raw_result),
                }
                continue
            if isinstance(raw_result, Mapping):
                results[source] = dict(raw_result)
            else:
                results[source] = {
                    "source_family": source,
                    "reason": "no_result",
                }
        return results

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

    async def _safe_one_shot_expression_copy(self, *, profile: Any) -> int:
        """Finish a non-daemon copy drain without creating background work."""

        callback = self.one_shot_expression_copy_callback
        if callback is None:
            return 0
        try:
            result = callback(profile)
            if inspect.isawaitable(result):
                result = await result
            return max(0, int(result or 0))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("one-shot expression-copy drain failed")
            return 0

    @staticmethod
    def _post_admission_copy_stage_is_owned(drain_result: object) -> bool:
        """Return whether a pipeline drain already ran its copy-stage owner.

        ``DiscoveryCandidatePipeline`` returns a mapping-compatible result with
        a structured post-admission receipt. Older test doubles and compatible
        third-party pipelines return a plain mapping, which deliberately means
        no owner was reported and therefore preserves the controller fallback.
        """

        receipt = getattr(drain_result, "post_admission_copy", None)
        return bool(getattr(receipt, "owns_copy_stage", False))

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
            ├─ candidate_eval             event continuous candidate evaluator
            ├─ _loop_soul_pipeline()     60s   profile updates, speculator
            ├─ _loop_bilibili_producer() 60s   Bili extension search fallback under cooldown
            ├─ _loop_xhs_producer()      60s   xhs keyword generation
            ├─ _loop_douyin_producer()   60s   Douyin discovery when under quota
            ├─ _loop_youtube_producer()  60s   YouTube discovery when under quota
            ├─ _loop_x_producer()        60s   X (Twitter) discovery when under quota
            ├─ _loop_zhihu_producer()    60s   Zhihu discovery when under quota
            ├─ _loop_reddit_producer()   60s   Reddit command-backed discovery when under quota
            ├─ _loop_bangumi_producer()  60s   Bangumi official-API discovery when under quota
            ├─ _loop_linuxdo_producer()  60s   Linux.do extension discovery when under quota
            ├─ _loop_weibo_producer()    60s   Weibo guest-session discovery when under quota
            ├─ _loop_proactive_push()    60s   delight + interest probe
            ├─ _loop_keyword_planner()  120s   P1.6 — merged keyword generation (flag-gated)
            ├─ _loop_source_incremental_sync() 60s  extension account refresh
            ├─ _loop_image_cache_cleanup() 6h  prune consumed+unsaved covers
            └─ _loop_cover_prefetch()    60s   cache fresh-token covers (XHS)
        """
        self.run_startup_maintenance()
        if self._llm_work_allowed():
            with suppress(Exception):
                await self.prepare_delight_candidates()
        self._warn_on_stranded_source_shares()
        # P1.6: give the keyword planner the controller's deficit / catalyst
        # 口径 so it shares the exact in-flight + raw-headroom accounting that
        # drives pool replenishment (it never recounts visible pool rows).
        if self.keyword_planner is not None:
            with suppress(Exception):
                self.keyword_planner.bind_deficit_source(self)
            bind_soul = getattr(self.keyword_planner, "bind_soul_engine", None)
            if callable(bind_soul):
                with suppress(Exception):
                    bind_soul(self.soul_engine)
        candidate_eval_loop = (
            self.candidate_eval_coordinator.run_forever()
            if self.candidate_eval_coordinator is not None
            else self._loop_candidate_eval()
        )
        expression_copy_loop = (
            self.expression_copy_coordinator.run_forever()
            if self.expression_copy_coordinator is not None
            else self._loop_pool_precompute()
        )
        tasks = [
            asyncio.create_task(self._loop_refresh()),
            asyncio.create_task(expression_copy_loop, name="expression_copy"),
            asyncio.create_task(candidate_eval_loop, name="candidate_eval"),
            asyncio.create_task(self._loop_soul_pipeline()),
            asyncio.create_task(self._loop_bilibili_producer()),
            asyncio.create_task(self._loop_xhs_producer()),
            asyncio.create_task(self._loop_douyin_producer()),
            asyncio.create_task(self._loop_youtube_producer()),
            asyncio.create_task(self._loop_x_producer()),
            asyncio.create_task(self._loop_zhihu_producer()),
            asyncio.create_task(self._loop_reddit_producer()),
            asyncio.create_task(self._loop_bangumi_producer()),
            asyncio.create_task(self._loop_linuxdo_producer()),
            asyncio.create_task(self._loop_v2ex_producer()),
            asyncio.create_task(self._loop_weibo_producer()),
            asyncio.create_task(self._loop_proactive_push()),
            asyncio.create_task(self._loop_keyword_planner()),
            asyncio.create_task(self._loop_image_cache_cleanup()),
            asyncio.create_task(self._loop_cover_prefetch()),
        ]
        if self.source_incremental_sync is not None:
            tasks.append(asyncio.create_task(self._loop_source_incremental_sync()))
        try:
            await asyncio.gather(*tasks)
        finally:
            candidate_stop = getattr(self.candidate_eval_coordinator, "stop", None)
            if callable(candidate_stop):
                await candidate_stop()
            expression_stop = getattr(self.expression_copy_coordinator, "stop", None)
            if callable(expression_stop):
                await expression_stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _loop_source_incremental_sync(self) -> None:
        """Poll the extension-online account refresh scheduler independently.

        The scheduler performs its own scheduler-enabled, presence, profile,
        and guided-init checks.  In particular, this loop never calls
        ``_llm_work_allowed``: periodic account collection is not LLM work.
        """
        scheduler = self.source_incremental_sync
        if scheduler is None:
            return
        while True:
            with suppress(Exception):
                await scheduler.tick()
            await asyncio.sleep(self.check_interval_seconds)

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
            elif not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
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
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._drain_pool_precompute_backlog()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_candidate_eval(self) -> None:
        """Drain pending discovery-candidate raw rows independently of refresh plans."""
        while True:
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                logger.debug("candidate eval drain skipped: reason=llm_paused")
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._drain_discovery_candidates_and_precompute(
                    reason="periodic",
                )
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
            self._update_llm_inventory_state(before_pool_count)
        except Exception:
            before_pool_count = -1
        try:
            if self.expression_copy_coordinator is None:
                await engine.precompute_pool_copy(
                    profile=profile, limit=_MAX_DISCOVERY_BACKFILL_PER_REFRESH
                )
            else:
                await engine.classify_pool_backlog(
                    profile=profile, limit=_MAX_DISCOVERY_BACKFILL_PER_REFRESH
                )
        except Exception:
            logger.exception("Periodic classify drain failed")
            return
        if self.expression_copy_coordinator is not None:
            self.expression_copy_coordinator.notify("safety_wake")
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

        state = self._update_discovery_runtime_state(
            lambda runtime_state: runtime_state.update(
                {"last_replenished_count": replenished_count}
            )
        )
        discovered_count = self._int_state_value(state, "last_discovered_count")
        recent_pool_topics = self._list_state_value(state, "recent_pool_topics")
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
        if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
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
            # Maintenance traffic: peak-hour deferral fully blocks it.
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_soul_pipeline()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_xhs_producer(self) -> None:
        """XHS keyword production — Soul-driven search task generation."""
        while True:
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_xhs_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_bilibili_producer(self) -> None:
        """Bilibili extension fallback — only enqueues while API search cools down."""
        while True:
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_bilibili_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_douyin_producer(self) -> None:
        """Douyin production — plugin/direct discovery when Douyin is below quota."""
        while True:
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_douyin_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_youtube_producer(self) -> None:
        """YouTube production — backend-direct discovery when YouTube is below quota."""
        while True:
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_youtube_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_x_producer(self) -> None:
        """X (Twitter) production — server-side cookie-replay discovery when under quota."""
        while True:
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_x_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_zhihu_producer(self) -> None:
        """Zhihu production — plugin-backed discovery when under quota."""
        while True:
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_zhihu_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_reddit_producer(self) -> None:
        """Reddit production — command-backed discovery when under quota."""
        while True:
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_reddit_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_bangumi_producer(self) -> None:
        """Bangumi production — official anonymous API discovery when under quota."""
        while True:
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_bangumi_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_linuxdo_producer(self) -> None:
        """Linux.do production — same-origin extension discovery when under quota."""
        while True:
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_linuxdo_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_v2ex_producer(self) -> None:
        """V2EX production — anonymous/public discovery with optional PAT."""
        while True:
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_v2ex_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_weibo_producer(self) -> None:
        """Run anonymous Weibo discovery when its source quota is underfilled."""

        while True:
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_weibo_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_keyword_planner(self) -> None:
        """P1.6: deficit-pulled merged keyword generation (flag-gated).

        Owns its own poll cadence (``planner_poll_seconds``) so a slow merged
        LLM call never blocks the 60s producer / refresh loops. The controller
        drives the planner per tick (rather than awaiting ``planner.run()``) so
        it can apply the same ``_llm_work_allowed`` gate every other LLM loop
        honours — pausing planning while a guided init runs or the extension is
        away. When ``keyword_planner`` is ``None`` (tests building the
        controller directly) or the feature flag is off, this is a no-op.
        """
        planner = self.keyword_planner
        if planner is None:
            return
        poll_seconds = max(1, int(getattr(planner, "poll_seconds", 120)))
        while True:
            if not bool(getattr(planner, "enabled", False)):
                await asyncio.sleep(poll_seconds)
                continue
            if not self._llm_work_allowed(pool_available=self._peak_refill_pool_available()):
                await asyncio.sleep(poll_seconds)
                continue
            with suppress(Exception):
                planner.reclaim_leases()
            with suppress(Exception):
                await planner.run_once()
            await asyncio.sleep(poll_seconds)

    async def _loop_proactive_push(self) -> None:
        """Delight + interest probe push — lightweight, never blocks.

        Runs on a longer cadence than the main refresh loop because
        probes/delight are not streaming content — once the active set
        has been delivered, additional pushes within minutes only
        contribute notification fatigue.
        """
        while True:
            # Proactive pushes are maintenance traffic: peak-hour deferral
            # fully blocks them (delight/probe scoring is not urgent).
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
                result = await asyncio.to_thread(
                    cleanup_image_cache,
                    database=self.database,
                )
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
        candidates = await asyncio.to_thread(
            self.database.iter_servable_cover_urls,
            recent_hours=_COVER_PREFETCH_RECENT_HOURS,
            limit=scan,
        )
        targets = await asyncio.to_thread(
            select_prefetch_targets,
            candidates,
            max_fetch=max_fetch,
        )
        coordinator = self.image_fetch_coordinator
        prefetch = coordinator.prefetch if coordinator is not None else prefetch_cover
        results = await asyncio.gather(*(prefetch(url) for url in targets))
        return sum(results)

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

    async def _tick_platform_producer(
        self,
        *,
        source_family: str,
        producer: Any | None,
        require_initialized: bool = True,
    ) -> dict[str, object]:
        """Run one source producer without overlapping another tick for that source."""

        if producer is None:
            return {"source_family": source_family, "reason": "not_configured"}
        if require_initialized and not self._is_initialized():
            return {"source_family": source_family, "reason": "not_initialized"}

        lock = self._producer_tick_locks.setdefault(source_family, asyncio.Lock())
        if lock.locked():
            return {"source_family": source_family, "reason": "in_flight"}

        async with lock:
            # Re-read after acquiring the source lock: another completed tick
            # may have filled the quota between the caller's wake and now.
            deficit = self._source_deficit(source_family)
            if deficit <= 0:
                return {"source_family": source_family, "reason": "quota_satisfied"}
            produce_fn = getattr(producer, "produce_if_due", None)
            if not callable(produce_fn):
                return {"source_family": source_family, "reason": "not_callable"}
            limit = max(1, min(deficit, self.discovery_limit))
            if _call_accepts_limit(produce_fn):
                raw_result = await produce_fn(limit=limit)
            else:
                raw_result = await produce_fn()
            result = dict(raw_result) if isinstance(raw_result, Mapping) else {}
            result.setdefault("source_family", source_family)
            result.setdefault("requested_limit", limit)
            return result

    async def _tick_xhs_producer(self) -> dict[str, object]:
        """Invoke the xhs search task producer if one is configured."""

        return await self._tick_platform_producer(
            source_family="xiaohongshu",
            producer=self.xhs_producer,
            require_initialized=False,
        )

    async def _tick_bilibili_producer(self) -> dict[str, object]:
        """Invoke the Bili extension fallback producer if Bilibili is under quota."""

        return await self._tick_platform_producer(
            source_family="bilibili",
            producer=self.bilibili_producer,
        )

    async def _tick_douyin_producer(self) -> dict[str, object]:
        """Invoke the Douyin discovery producer if Douyin is under quota."""

        return await self._tick_platform_producer(
            source_family="douyin",
            producer=self.douyin_producer,
        )

    async def _tick_youtube_producer(self) -> dict[str, object]:
        """Invoke the YouTube discovery producer if YouTube is under quota."""

        return await self._tick_platform_producer(
            source_family="youtube",
            producer=self.youtube_producer,
        )

    async def _tick_x_producer(self) -> dict[str, object]:
        """Invoke the X (Twitter) discovery producer if X is under quota."""

        return await self._tick_platform_producer(
            source_family="twitter",
            producer=self.x_producer,
        )

    async def _tick_zhihu_producer(self) -> dict[str, object]:
        """Invoke the Zhihu discovery producer if Zhihu is under quota."""

        return await self._tick_platform_producer(
            source_family="zhihu",
            producer=self.zhihu_producer,
        )

    async def _tick_reddit_producer(self) -> dict[str, object]:
        """Invoke the Reddit discovery producer if Reddit is under quota."""

        return await self._tick_platform_producer(
            source_family="reddit",
            producer=self.reddit_producer,
        )

    async def _tick_bangumi_producer(self) -> dict[str, object]:
        """Invoke Bangumi discovery when its source-family quota has a deficit."""

        return await self._tick_platform_producer(
            source_family="bangumi",
            producer=self.bangumi_producer,
        )

    async def _tick_linuxdo_producer(self) -> dict[str, object]:
        """Invoke Linux.do discovery when its source-family quota has a deficit."""

        return await self._tick_platform_producer(
            source_family="linuxdo",
            producer=self.linuxdo_producer,
        )

    async def _tick_v2ex_producer(self) -> dict[str, object]:
        """Invoke V2EX discovery when its source-family quota has a deficit."""

        return await self._tick_platform_producer(
            source_family="v2ex",
            producer=self.v2ex_producer,
        )

    async def _tick_weibo_producer(self) -> dict[str, object]:
        """Invoke Weibo discovery when its source-family quota has a deficit."""

        return await self._tick_platform_producer(
            source_family="weibo",
            producer=self.weibo_producer,
        )

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
        self._update_llm_inventory_state(pool_available)
        pool_below_target = pool_available < self.pool_target_count

        if pool_below_target:
            if not self._pool_below_replenishment_watermark(pool_available):
                return []
            source_plan = self._build_source_replenishment_plan()
            if source_plan:
                return source_plan
            # No source has an own-share deficit. That includes the
            # healthy-source stall seen in production: Bilibili (and often
            # XHS/Reddit) are already at/over quota while the missing
            # capacity belongs to sources that are missing, throttled, or
            # rate-limited (V2EX CLI absent, X unhealthy, YouTube/Weibo
            # cooling down). If the discovery-candidate pipeline still has
            # claimed/evaluating work, let it drain first; otherwise fall
            # through to the periodic Bilibili plan so healthy over-share
            # sources can keep introducing fresh topics and fill the global
            # pool. Pool-share rebalancing can still demote them later when
            # the under-share sources recover.
            readiness = self._pool_readiness_counts()
            if int(readiness.get("pending_eval", 0) or 0) > 0:
                self._log_empty_refresh_plan_diagnostics(pool_available=pool_available)
                return []
        if "bilibili" not in self._normalized_pool_source_shares():
            self._log_empty_refresh_plan_diagnostics(pool_available=pool_available)
            return []

        plan: list[tuple[list[str], int]] = []
        if pending_events >= self.signal_event_threshold:
            plan.append((["search", "related_chain"], self.discovery_limit))
        if self._is_due(
            str(state.get("last_trending_refresh_at", "")),
            minutes=self.trending_refresh_minutes,
        ):
            plan.append((["trending"], self.discovery_limit))
        if self._is_due(
            str(state.get("last_explore_refresh_at", "")),
            minutes=self.explore_refresh_minutes,
        ):
            plan.append((["explore"], self.discovery_limit))
        if not plan:
            self._log_empty_refresh_plan_diagnostics(pool_available=pool_available)
        return plan

    def _pool_below_replenishment_watermark(self, pool_available: int) -> bool:
        target = max(1, int(self.pool_target_count))
        low_watermark = int(target * _DISCOVERY_REPLENISH_LOW_WATERMARK_RATIO)
        return int(pool_available) < low_watermark

    def _log_empty_refresh_plan_diagnostics(self, *, pool_available: int) -> None:
        now = self._now()
        fingerprint = (max(0, int(pool_available)),)
        last_at = self._last_empty_plan_diag_at
        full_diagnostics_due = (
            last_at is None
            or fingerprint != self._last_empty_plan_fingerprint
            or (now - last_at).total_seconds() >= _EMPTY_PLAN_DIAG_INTERVAL_SECONDS
        )
        if not full_diagnostics_due:
            self._suppressed_empty_plan_count += 1
            logger.debug(
                "refresh plan empty (suppressed diagnostics, %d since last full)",
                self._suppressed_empty_plan_count,
            )
            return

        suppressed_count = self._suppressed_empty_plan_count
        self._last_empty_plan_diag_at = now
        self._last_empty_plan_fingerprint = fingerprint
        self._suppressed_empty_plan_count = 0
        try:
            readiness = self._pool_readiness_counts()
        except Exception:
            logger.debug("refresh plan empty readiness diagnostics failed", exc_info=True)
            readiness = {}
        try:
            source_available = self._count_pool_available_candidates_by_source()
        except Exception:
            logger.debug("refresh plan empty source available diagnostics failed", exc_info=True)
            source_available = {}
        try:
            source_raw = self._count_pool_raw_material_by_source()
        except Exception:
            logger.debug("refresh plan empty source raw diagnostics failed", exc_info=True)
            source_raw = {}
        source_targets = self._source_target_counts()
        raw_targets = self._raw_source_target_counts()
        requested_by_source: dict[str, int] = {}
        sources = sorted(
            set(source_targets)
            | set(raw_targets)
            | set(source_available)
            | set(source_raw)
            | set(_PLATFORM_SOURCE_ORDER)
        )
        for source in sources:
            try:
                requested_by_source[source] = self._source_requested_count(
                    source,
                    source_available_counts=source_available,
                    source_raw_counts=source_raw,
                    target_counts=source_targets,
                    raw_target_counts=raw_targets,
                )
            except Exception:
                logger.debug(
                    "refresh plan empty requested_by_source diagnostics failed for %s",
                    source,
                    exc_info=True,
                )
                requested_by_source[source] = -1

        logger.info(
            "refresh plan empty: pool_available=%s raw=%s pending=%s suppressed=%s "
            "source_available=%s source_raw=%s source_targets=%s raw_targets=%s "
            "requested_by_source=%s",
            pool_available,
            readiness.get("raw", "?"),
            readiness.get("pending", "?"),
            suppressed_count,
            source_available,
            source_raw,
            source_targets,
            raw_targets,
            requested_by_source,
        )

    async def refresh_after_event_ingest(self) -> dict[str, object]:
        """Compatibility shim: event ingest marks demand, scheduler refreshes later."""
        return self._queue_replenishment_reason("event_ingest")

    async def refresh_after_feedback(self) -> dict[str, object]:
        """Compatibility shim: feedback marks demand, scheduler refreshes later."""
        self.notify_expression_copy_pending("feedback")
        return self._queue_replenishment_reason("feedback")

    async def refresh_after_init(self) -> dict[str, object]:
        """Compatibility shim: init completion should kick replenishment now."""
        return await self.request_replenishment(reason="init_completed", force=True)

    async def drain_discovery_candidates_once(
        self,
        *,
        batch_size: int | None = None,
        reason: str = "manual",
    ) -> dict[str, int]:
        """Drain one pending discovery-candidate batch through the shared evaluator."""

        notify = getattr(self.candidate_eval_coordinator, "notify", None)
        if callable(notify):
            notify(reason)
            return {"evaluated": 0, "cached": 0, "rejected": 0}
        return await self._drain_discovery_candidates_and_precompute(
            reason=reason,
            batch_size=batch_size,
            precompute=False,
        )

    async def _drain_discovery_candidates_and_precompute(
        self,
        *,
        reason: str,
        batch_size: int | None = None,
        profile: Any | None = None,
        precompute: bool = True,
    ) -> dict[str, int]:
        """Drain one pending raw-candidate batch and optionally precompute it."""

        pipeline = self.discovery_candidate_pipeline
        if pipeline is None:
            logger.debug("candidate eval drain skipped: reason=no_pipeline caller=%s", reason)
            return {"evaluated": 0, "cached": 0, "rejected": 0}
        if self._discovery_drain_lock.locked():
            logger.debug("candidate eval drain skipped: reason=locked caller=%s", reason)
            return {"evaluated": 0, "cached": 0, "rejected": 0}
        async with self._discovery_drain_lock:
            # Pool-share fairness (spec 2026-07-20, Phase 3/4): before measuring
            # the pool, gently evict a few over-share rows if an under-share
            # source has supply waiting, and log the deficit summary. This frees
            # slots the same tick so the admission below (and the pool_at_cap
            # gate) reflect the freed room. Shared with the coordinator assembly
            # via the same entry point (D7).
            self.run_pool_share_maintenance()
            try:
                pool_available = self.database.count_pool_candidates(
                    xhs_self_nickname=self._xhs_self_nickname()
                )
            except TypeError:
                pool_available = self.database.count_pool_candidates()
            before_pool_count = int(pool_available)
            self._update_llm_inventory_state(before_pool_count)
            if int(pool_available) >= self.pool_target_count:
                logger.debug(
                    "candidate eval drain skipped: reason=pool_at_cap "
                    "pool_available=%s target=%s caller=%s",
                    pool_available,
                    self.pool_target_count,
                    reason,
                )
                return {"evaluated": 0, "cached": 0, "rejected": 0}
            if profile is None:
                try:
                    profile = await self.soul_engine.get_profile()
                except Exception as exc:
                    logger.info(
                        "candidate eval drain skipped: reason=no_profile caller=%s error=%s",
                        reason,
                        exc,
                    )
                    return {"evaluated": 0, "cached": 0, "rejected": 0}
            if profile is None:
                logger.info("candidate eval drain skipped: reason=no_profile caller=%s", reason)
                return {"evaluated": 0, "cached": 0, "rejected": 0}
            result = await pipeline.drain_pending(
                profile=profile,
                batch_size=self._candidate_eval_drain_batch_size(batch_size),
            )
            drain_result = cast("dict[str, int]", result)
            evaluated = int(drain_result.get("evaluated", 0) or 0)
            cached = int(drain_result.get("cached", 0) or 0)
            rejected = int(drain_result.get("rejected", 0) or 0)
            failed = int(drain_result.get("failed", 0) or 0)
            waiting = int(drain_result.get("waiting", 0) or 0)
            post_admission_copy_owned = self._post_admission_copy_stage_is_owned(drain_result)
        if cached > 0 and precompute:
            if self.expression_copy_coordinator is not None:
                self.expression_copy_coordinator.notify(f"candidate_admitted:{cached}")
            elif self.one_shot_expression_copy_callback is not None:
                if not post_admission_copy_owned:
                    await self._safe_one_shot_expression_copy(profile=profile)
            else:
                await self._safe_precompute_pool_copy(profile=profile)
                await self._publish_precompute_replenishment_if_needed(
                    before_pool_count=before_pool_count
                )
        if evaluated or cached or rejected or failed:
            logger.info(
                "candidate eval drain done: caller=%s evaluated=%s cached=%s rejected=%s failed=%s",
                reason,
                evaluated,
                cached,
                rejected,
                failed,
            )
        elif waiting:
            logger.info(
                "candidate eval drain skipped: reason=batch_waiting pending=%s caller=%s",
                waiting,
                reason,
            )
        else:
            logger.debug("candidate eval drain skipped: reason=no_pending caller=%s", reason)
        return drain_result

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
        post_admission_copy_owned = False

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

            effective_limit = self._requested_refresh_limit(
                requested_limit=requested_limit,
                current_pool_count=current_pool_count,
                pool_below_target=initial_pool_below_target,
            )
            effective_limit = self._bounded_one_shot_inline_eval_limit(effective_limit)
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
            # Unified keyword planner fetch path (P1.7, flag-gated). B站 search is
            # inline-admit: this plan iteration fetches + drains (admits) in the
            # same call. When the flag is on and this entry includes ``search``,
            # claim words from the store and inject them as ``keywords`` (the
            # engine maps them onto the search strategy's ``queries`` param). If
            # the planner store is empty, remove only ``search`` from this batch:
            # passing queries=None would re-enable the legacy LLM generator.
            claimed_search: list[Any] = []
            coordinator = self.keyword_fetch
            should_claim_search = (
                "search" in strategies
                and coordinator is not None
                and bool(getattr(coordinator, "should_claim", lambda: False)())
                and int(current_pool_counts.get("pending_eval", 0) or 0) < effective_limit
            )
            effective_strategies = list(strategies)
            if should_claim_search and coordinator is not None:
                claimed_search = coordinator.claim(_KW_PLATFORM_BILIBILI)
                if not claimed_search:
                    effective_strategies = [
                        strategy for strategy in effective_strategies if strategy != "search"
                    ]
                    logger.info(
                        "bili search skipped: unified keyword planner enabled but no "
                        "pending keywords; running strategies=%s",
                        ",".join(effective_strategies) or "none",
                    )
                    if not effective_strategies:
                        continue
            if strategy_limits:
                filtered_strategy_limits = {
                    strategy: limit
                    for strategy, limit in strategy_limits.items()
                    if strategy in effective_strategies
                }
                if should_claim_search and not claimed_search and effective_strategies:
                    dropped_budget = max(0, int(strategy_limits.get("search", 0) or 0))
                    if dropped_budget > 0:
                        budget_target = (
                            "related_chain"
                            if "related_chain" in effective_strategies
                            else effective_strategies[0]
                        )
                        filtered_strategy_limits[budget_target] = (
                            max(0, int(filtered_strategy_limits.get(budget_target, 0) or 0))
                            + dropped_budget
                        )
                effective_strategy_limits = filtered_strategy_limits or None
            else:
                effective_strategy_limits = None
            injected_keywords = (
                [item.keyword for item in claimed_search] if claimed_search else None
            )
            # P1.8 yield provenance: ``query → keyword id`` for the claimed words
            # so each produced candidate carries ``source_keyword_id`` for
            # admit-time yield backfill. Empty / None on the flag-off path.
            injected_keyword_ids = (
                {item.keyword: int(item.id) for item in claimed_search} if claimed_search else None
            )

            await self._publish_event(
                {
                    "type": "refresh.strategy",
                    "phase": "running",
                    "strategy": "+".join(effective_strategies),
                    "message": self._strategy_message(effective_strategies),
                    **self._pool_count_payload(current_pool_counts),
                }
            )

            pipeline = self.discovery_candidate_pipeline
            discovered: list[Any] = []
            topic_items: list[Any] = []
            discovered_count = 0
            admitted_count = 0
            iteration_failed = False
            try:
                if pipeline is not None:
                    produce_kwargs: dict[str, Any] = {
                        "profile": profile,
                        "strategies": effective_strategies,
                        "limit": effective_limit,
                        "strategy_limits": effective_strategy_limits,
                        "pool_snapshot": pool_snapshot,
                    }
                    if injected_keywords is not None:
                        produce_kwargs["keywords"] = injected_keywords
                    if injected_keyword_ids:
                        produce_kwargs["keyword_ids"] = injected_keyword_ids
                    ensure_supply = getattr(pipeline, "ensure_pending_supply", None)
                    if callable(ensure_supply):
                        supply_result = await ensure_supply(
                            **produce_kwargs,
                            target_pending=effective_limit,
                        )
                        produced_count = int(
                            dict(supply_result).get("inserted", 0)
                            if isinstance(supply_result, dict)
                            else 0
                        )
                    else:
                        produced_count = await pipeline.produce_and_enqueue(**produce_kwargs)
                    coordinator_notify = getattr(self.candidate_eval_coordinator, "notify", None)
                    if callable(coordinator_notify):
                        # API runtime wires ``pipeline.on_candidates_enqueued`` to
                        # this coordinator.  That callback is the one immediate
                        # wake for every successful enqueue; doing an inline
                        # drain here would create a second durable claim owner
                        # and could take the 3×30 cap to 180 rows.
                        drain_result = {"evaluated": 0, "cached": 0, "rejected": 0}
                    else:
                        # One-shot / legacy compositions deliberately have no
                        # live coordinator, so preserve their bounded inline
                        # drain semantics.
                        drain_result = await self._drain_discovery_candidates_and_precompute(
                            reason="refresh",
                            profile=profile,
                            batch_size=effective_limit,
                            precompute=False,
                        )
                    post_admission_copy_owned = (
                        post_admission_copy_owned
                        or self._post_admission_copy_stage_is_owned(drain_result)
                    )
                    discovered_count = int(produced_count or 0)
                    admitted_count = int(drain_result.get("cached", 0) or 0)
                    if admitted_count > 0:
                        topic_items = list(getattr(pipeline, "last_admitted_items", []) or [])
                    pipeline_discovered_count += discovered_count
                else:
                    discover_fn = self.discovery_engine.discover
                    discover_kwargs: dict[str, Any] = {
                        "strategies": effective_strategies,
                        "limit": effective_limit,
                    }
                    if effective_strategy_limits and _call_accepts_strategy_limits(discover_fn):
                        discover_kwargs["strategy_limits"] = effective_strategy_limits
                    if _call_accepts_pool_snapshot(discover_fn):
                        discover_kwargs["pool_snapshot"] = pool_snapshot
                    if injected_keywords is not None and _call_accepts_keywords(discover_fn):
                        discover_kwargs["keywords"] = injected_keywords
                    if injected_keyword_ids and _call_accepts_keyword_ids(discover_fn):
                        discover_kwargs["keyword_ids"] = injected_keyword_ids
                    discovered = await discover_fn(profile, **discover_kwargs)
                    topic_items = discovered
                    discovered_count = len(discovered)
                    admitted_count = discovered_count
            except Exception:
                iteration_failed = True
                if claimed_search and coordinator is not None:
                    coordinator.mark_failed(claimed_search)
                raise
            finally:
                if claimed_search and coordinator is not None and not iteration_failed:
                    # Inline-admit terminal: words that drove a fetch producing
                    # candidates are ``used``; an empty fetch marks them ``failed``
                    # (retry). yield backfill is P1.8, decoupled from ``used``.
                    if discovered_count > 0:
                        coordinator.mark_used(claimed_search)
                    else:
                        coordinator.mark_failed(claimed_search)
            all_discovered.extend(discovered)
            flattened_strategies.extend(effective_strategies)

            if admitted_count > 0:
                replenished_topics.extend(self._extract_topics(topic_items))
                if self.expression_copy_coordinator is not None:
                    self.expression_copy_coordinator.notify(f"refresh_admitted:{admitted_count}")

        if flattened_strategies:
            # Snapshot delight count BEFORE precompute so we can detect
            # net new above-threshold delights and push a refresh event
            # to the popup (no per-item chrome notification — popup
            # re-fetches /api/delight/pending-batch when this fires).
            delight_count_before = self._safe_count_delight_candidates()
            if self.expression_copy_coordinator is not None:
                self.expression_copy_coordinator.notify("refresh_complete")
            elif self.one_shot_expression_copy_callback is not None:
                if not post_admission_copy_owned:
                    await self._safe_one_shot_expression_copy(profile=profile)
            else:
                await self._safe_precompute_pool_copy(profile=profile)
            # Pre-warm supergroup-merge embeddings so the popup's "换一批"
            # hot path always hits the L1/L2 cache. These are daemon latency
            # optimizations, not part of a one-shot adapter's completed
            # operation; OpenClaw deliberately leaves no provider-backed
            # background task behind after returning.
            if self.one_shot_expression_copy_callback is None:
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

            # Land all newly durable admissions through the one atomic
            # topic/source/stale/raw maintenance boundary before the popup
            # re-fetches. No separately committed destructive pass belongs
            # in this refresh plan.
            try:
                await self._enforce_pool_cap_async(force_scan=True)
            except Exception:
                logger.exception("post-refresh enforce_pool_cap failed")

        now = self._now().isoformat()
        latest_event_id = self.database.get_latest_event_id()
        runtime_updates: dict[str, object] = {}
        if "search" in flattened_strategies or "related_chain" in flattened_strategies:
            runtime_updates["last_event_refresh_at"] = now
            runtime_updates["last_processed_event_id"] = latest_event_id
        if "trending" in flattened_strategies:
            runtime_updates["last_trending_refresh_at"] = now
        if "explore" in flattened_strategies:
            runtime_updates["last_explore_refresh_at"] = now
        after_pool_counts = self._pool_readiness_counts()
        after_pool_count = after_pool_counts["available"]
        supply_inserted_count = len(all_discovered) + pipeline_discovered_count
        runtime_updates["last_discovered_count"] = supply_inserted_count
        runtime_updates["last_replenished_count"] = max(0, after_pool_count - before_pool_count)
        if replenished_topics:
            runtime_updates["recent_pool_topics"] = self._dedupe_topics(replenished_topics)[:3]
        state = self._update_discovery_runtime_state(
            lambda runtime_state: runtime_state.update(runtime_updates)
        )
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
            "supply_inserted_count": supply_inserted_count,
            "supply_productive": supply_inserted_count > 0,
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
            pool_counts = await self._pool_readiness_counts_async()
            current = int(pool_counts["available"])
        except Exception:
            return
        if current == self._last_published_pool_count:
            return
        self._last_published_pool_count = current
        payload: dict[str, object] = {
            "type": "pool_status",
            **self._pool_count_payload(pool_counts),
            "pool_target_count": int(self.pool_target_count),
        }
        status_payload = getattr(self.candidate_eval_coordinator, "status_payload", None)
        if callable(status_payload):
            with suppress(Exception):
                payload.update(status_payload())
        await self._publish_event(payload)
        if current < int(self.pool_target_count):
            notify = getattr(self.candidate_eval_coordinator, "notify", None)
            if callable(notify):
                notify("inventory_consumed")

    def _safe_count_delight_candidates(self) -> int:
        """Best-effort count of pending delight candidates (returns 0 on any
        error so the caller can do delta-based comparison without crashing
        the refresh tick)."""
        try:
            return int(
                self.database.count_delight_candidates(
                    min_delight_score=self._dynamic_delight_threshold()
                )
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
                "item_key": candidate.get("item_key", ""),
                "content_id": candidate.get("content_id", "") or candidate.get("bvid", ""),
                "title": candidate.get("title", ""),
                "delight_reason": candidate.get("delight_reason", ""),
                "delight_score": candidate.get("delight_score", 0.0),
                "delight_hook": candidate.get("delight_hook", ""),
                "cover_url": candidate.get("cover_url", ""),
                "content_url": candidate.get("content_url", ""),
                "source_platform": candidate.get("source_platform", "bilibili"),
                "published_at": str(candidate.get("published_at", "") or ""),
                "published_label": str(candidate.get("published_label", "") or ""),
                "content_type": candidate.get("content_type", "video"),
                "body_text": candidate.get("body_text", ""),
                # Engagement stats so a live-pushed delight card shows the same
                # ▶/👍/💬/🔁 row as the pending-batch path (0 = not fetched). Passed
                # through as-is like the other row fields; the client coerces.
                "view_count": candidate.get("view_count", 0),
                "like_count": candidate.get("like_count", 0),
                "comment_count": candidate.get("comment_count", 0),
                "share_count": candidate.get("share_count", 0),
                "danmaku_count": candidate.get("danmaku_count", 0),
                "favorite_count": candidate.get("favorite_count", 0)
                or candidate.get("collect_count", 0),
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
        specs = [
            spec
            for spec in get_active()
            if str(getattr(spec, "status", "active")).strip().lower() == "active"
        ]
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
        delivered_at = now.isoformat()

        def _record_probe(runtime_state: dict[str, object]) -> None:
            latest_probed = _string_state_map(runtime_state.get("probed_domains"))
            latest_probed[domain.lower()] = delivered_at
            runtime_state["probed_domains"] = latest_probed
            latest_axes = _string_state_map(runtime_state.get("probed_axes"))
            if axis:
                latest_axes[axis] = delivered_at
            runtime_state["probed_axes"] = latest_axes
            latest_bands = _string_state_map(runtime_state.get("probed_distance_bands"))
            latest_bands[probe_mode] = delivered_at
            runtime_state["probed_distance_bands"] = latest_bands

        self._update_discovery_runtime_state(_record_probe)
        return True

    async def _publish_avoidance_probe_if_available(self) -> bool:
        """Push the top speculative-avoidance hypothesis via WebSocket."""
        speculator = getattr(self.soul_engine, "_avoidance_speculator", None)
        get_active = getattr(speculator, "get_active_avoidances", None)
        if not callable(get_active):
            return False
        avoidances = [
            avoidance
            for avoidance in get_active()
            if str(getattr(avoidance, "status", "active")).strip().lower() == "active"
        ]
        if not avoidances:
            return False

        state = self.memory_manager.load_discovery_runtime_state()
        probed = _string_state_map(state.get("probed_avoidance_domains"))
        probed_axes = _string_state_map(state.get("probed_avoidance_axes"))
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

        delivered_at = now.isoformat()

        def _record_avoidance_probe(runtime_state: dict[str, object]) -> None:
            latest_probed = _string_state_map(runtime_state.get("probed_avoidance_domains"))
            latest_probed[domain.lower()] = delivered_at
            runtime_state["probed_avoidance_domains"] = latest_probed
            latest_axes = _string_state_map(runtime_state.get("probed_avoidance_axes"))
            if axis:
                latest_axes[axis] = delivered_at
            runtime_state["probed_avoidance_axes"] = latest_axes

        self._update_discovery_runtime_state(_record_avoidance_probe)
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

            def _record_last_probe_kind(
                runtime_state: dict[str, object],
                *,
                probe_kind: str = kind,
            ) -> None:
                runtime_state["last_probe_kind"] = probe_kind

            self._update_discovery_runtime_state(_record_last_probe_kind)
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

    def _waiting_supply_by_family(self) -> dict[str, int]:
        """Return admission-waiting candidate counts per family.

        Pool-share fairness (spec 2026-07-20, Phase 8 / D9): counts every
        non-terminal stage (``pending_eval`` + ``evaluating`` + ``evaluated``),
        not just ``evaluated`` — an orphan occupier pins the pool full, so an
        under-share source never reaches ``evaluated`` and eviction would never
        fire if we only counted the last stage. Falls back to the older
        evaluated-only counter if the wider one is unavailable.
        """
        count_fn = getattr(
            self.database, "count_admission_waiting_discovery_candidates_by_source", None
        )
        if not callable(count_fn):
            count_fn = getattr(
                self.database, "count_evaluated_discovery_candidates_by_source", None
            )
        if not callable(count_fn):
            return {}
        try:
            counts = count_fn()
        except Exception:
            logger.debug("waiting-supply-by-source snapshot failed", exc_info=True)
            return {}
        return {str(family): int(count) for family, count in dict(counts).items()}

    def _rebalance_pool_shares(self) -> int:
        """Gently free over-share slots for under-share sources with supply waiting.

        Pool-share fairness (spec 2026-07-20, Phase 3). Runs inside the drain
        tick before admission. Only acts when the visible pool is at/over
        target AND some under-share source has ``evaluated`` supply waiting to
        take a freed slot. Evicts at most ``_POOL_REBALANCE_MAX_PER_TICK`` (3)
        rows from the single most over-share source — the lowest-scored oldest
        rows — so the pool converges toward the configured shares at a calm,
        quality-preserving rate. Never touches under-share or at-share sources.
        """
        demote_fn = getattr(self.database, "demote_lowest_ranked_pool_rows", None)
        if not callable(demote_fn):
            return 0
        try:
            available_total = self.database.count_pool_candidates(
                xhs_self_nickname=self._xhs_self_nickname()
            )
        except TypeError:
            available_total = self.database.count_pool_candidates()
        except Exception:
            logger.debug("pool rebalance pool-count read failed", exc_info=True)
            return 0
        if int(available_total) < self.pool_target_count:
            return 0

        target_counts = self._source_target_counts()
        available_by_source = self._count_pool_available_candidates_by_source()
        waiting_by_source = self._waiting_supply_by_family()

        # Budget = how many freed slots an under-share source could actually
        # fill soon (deficit clamped by its admission-waiting supply — any of
        # pending_eval / evaluating / evaluated, see _waiting_supply_by_family).
        fillable = 0
        for family, target in target_counts.items():
            deficit = int(target) - self._platform_source_count(available_by_source, family)
            if deficit > 0:
                fillable += min(deficit, int(waiting_by_source.get(family, 0)))
        if fillable <= 0:
            return 0

        # Pick the single most over-share source. Candidate families are the
        # configured targets PLUS any family actually holding visible slots but
        # absent from target_counts — a source the user disabled leaves stranded
        # rows that must count as fully over-share (target 0), else those rows
        # permanently squat the pool and an under-share source's deficit can
        # never be freed (spec D8). Available keys are normalized to families
        # first so a disabled bilibili surfacing under its four strategy names
        # merges to one "bilibili" overage (note: _pool_source_family口径).
        candidate_families: set[str] = set(target_counts)
        for source_key in available_by_source:
            candidate_families.add(_source_family(source_key, source_key))
        over_share: list[tuple[int, str]] = []
        for family in candidate_families:
            overage = self._platform_source_count(available_by_source, family) - int(
                target_counts.get(family, 0)
            )
            if overage > 0:
                over_share.append((overage, family))
        if not over_share:
            return 0
        over_share.sort(reverse=True)
        overage, family = over_share[0]

        demote_count = min(_POOL_REBALANCE_MAX_PER_TICK, overage, fillable)
        if demote_count <= 0:
            return 0
        try:
            demoted = int(demote_fn(source_family=family, limit=demote_count) or 0)
        except Exception:
            logger.debug("pool rebalance demotion failed", exc_info=True)
            return 0
        if demoted:
            logger.info(
                "pool rebalance: demoted %d over-share '%s' row(s) (overage=%d) to free "
                "slots for %d under-share row(s) waiting admission",
                demoted,
                family,
                overage,
                fillable,
            )
        return demoted

    def run_pool_share_maintenance(self) -> None:
        """Run share rebalance + deficit summary once per candidate-eval tick.

        Pool-share fairness (spec 2026-07-20, Phase 3/4, wired per D7). This is
        the single entry point invoked by BOTH candidate-eval assemblies —
        legacy ``_loop_candidate_eval`` → ``_drain_discovery_candidates_and_precompute``
        and the production ``CandidateEvalCoordinator`` (via its pre-admit
        hook). The two are mutually exclusive at ``run_forever`` wiring
        (``coordinator.run_forever()`` XOR ``_loop_candidate_eval()``), so this
        never double-runs in a single tick. Errors are swallowed so pool
        maintenance can never break the eval loop.
        """
        with suppress(Exception):
            self._rebalance_pool_shares()
        with suppress(Exception):
            self._log_source_deficit_summary()

    def _log_source_deficit_summary(self) -> None:
        """Log a one-line per-source available/target/deficit summary on change.

        Pool-share fairness (spec 2026-07-20, Phase 4). This排查 cost us three
        layers of silent skips; a change-throttled INFO line makes the share
        picture legible without spamming the log every steady tick.
        """
        target_counts = self._source_target_counts()
        if not target_counts:
            return
        available_by_source = self._count_pool_available_candidates_by_source()
        snapshot: dict[str, tuple[int, int, int]] = {}
        for family, target in target_counts.items():
            available = self._platform_source_count(available_by_source, family)
            deficit = self._source_deficit(family)
            snapshot[family] = (available, int(target), int(deficit))
        if snapshot == self._last_source_deficit_snapshot:
            return
        self._last_source_deficit_snapshot = snapshot
        summary = " | ".join(
            f"{family} {available}/{target} (deficit {deficit})"
            for family, (available, target, deficit) in sorted(snapshot.items())
        )
        logger.info("pool source shares: %s", summary)

    # ── keyword planner deficit / catalyst口径 (P1.6) ─────────────────────
    # The unified keyword planner reuses these so its "real deficit" shares the
    # exact available-pool deficit口径 that drives pool replenishment, instead of
    # naively counting visible pool rows. Raw headroom still caps normal request
    # size, but cannot turn an under-target available pool into "no deficit".

    def keyword_planner_real_deficit(self, platform: str) -> int:
        """Real search deficit for one platform.

        Wraps ``_source_requested_count`` — the same口径 used by
        ``_build_source_replenishment_plan``. ``> 0`` means the platform
        genuinely needs more search supply.
        """
        try:
            return int(self._source_requested_count(str(platform).strip()))
        except Exception:
            logger.exception("keyword_planner_real_deficit failed for %s", platform)
            return 0

    def keyword_planner_bilibili_catalyst(self) -> bool:
        """B站's extra catalyst: pool-below-target OR ≥ signal-event threshold.

        Mirrors ``_build_refresh_plan`` — B站 search regenerates keywords when
        the pool is below target (its four strategies fire together) or when
        ≥ ``signal_event_threshold`` signal events have queued (profile may have
        just drifted), even if its keyword cache is not below the low watermark.
        """
        try:
            pool_available = self.database.count_pool_candidates(
                xhs_self_nickname=self._xhs_self_nickname()
            )
        except TypeError:
            pool_available = self.database.count_pool_candidates()
        except Exception:
            logger.exception("keyword_planner_bilibili_catalyst pool count failed")
            return False
        if int(pool_available) < self.pool_target_count:
            return True
        try:
            state = self.memory_manager.load_discovery_runtime_state()
            pending_events = self._pending_signal_events_count(state)
        except Exception:
            logger.exception("keyword_planner_bilibili_catalyst signal count failed")
            return False
        return pending_events >= self.signal_event_threshold

    def keyword_planner_explore_due_soon(self) -> bool:
        """Whether planner may piggyback B站 exploratory queries this pass.

        This intentionally mirrors refresh-plan timing instead of giving the
        planner its own clock. The small lead window lets a planner pass that
        runs just before the refresh tick reuse the merged keyword LLM call for
        explore, avoiding the later standalone ``discovery.explore.queries``
        call while still respecting ``explore_refresh_minutes``.
        """
        if "bilibili" not in self._normalized_pool_source_shares():
            return False
        if self.keyword_planner_real_deficit("bilibili") <= 0:
            return False
        try:
            state = self.memory_manager.load_discovery_runtime_state()
        except Exception:
            logger.exception("keyword_planner_explore_due_soon state load failed")
            return False
        return self._is_due_soon(
            str(state.get("last_explore_refresh_at", "")),
            minutes=self.explore_refresh_minutes,
            lead_seconds=max(0, int(self.check_interval_seconds)),
        )

    def keyword_planner_explore_covered_topic_groups(self) -> list[str]:
        """Covered pool topic_groups for the planner's exploratory query block."""
        getter = getattr(self.database, "get_active_pool_topic_groups", None)
        if not callable(getter):
            return []
        try:
            return [
                str(item).strip() for item in getter(limit=12, min_count=2) if str(item).strip()
            ]
        except Exception:
            logger.debug("keyword planner covered topic-group lookup failed", exc_info=True)
            return []

    def keyword_planner_mark_explore_planned(self) -> None:
        """Mark explore refresh consumed after planner inserted explore queries."""
        now = self._now().isoformat()
        self._update_discovery_runtime_state(
            lambda runtime_state: runtime_state.update({"last_explore_refresh_at": now})
        )

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
        # Keep the durable inventory observation, but do NOT clamp per-source
        # deficit by the global headroom. Pool-share fairness spec (2026-07-20,
        # invariant 2): once the global pool is full, an over-supplied source
        # (e.g. reddit 169/25) would zero out every under-share source's
        # deficit via ``min(available_deficit, global_available_deficit)``,
        # starving its producer forever. Own-share deficit is now bounded only
        # by the per-source raw ceiling; admission stays globally capped so the
        # visible pool never overshoots ``pool_target_count``.
        self._update_llm_inventory_state(current_global_available)
        raw_target = int(raw_target_counts.get(source_family, 0))
        current_raw = self._platform_source_count(source_raw_counts, source_family)
        raw_headroom = max(0, raw_target - current_raw)
        requested_by_available = available_deficit
        if requested_by_available <= 0:
            return 0
        if raw_headroom > 0:
            return min(requested_by_available, raw_headroom)
        # Raw ceiling is a trimming guard, not a hard stop for replenishment.
        # A pool can have enough raw material but still be far below the
        # frontend-servable target because existing rows are blocked by topic
        # windows, linkability, copied text/category readiness, or recommendation
        # history. In that state, returning 0 strands pending keywords and leaves
        # the scheduler alive but unable to search.
        return requested_by_available

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
            elif source == "twitter" and self.x_producer is None:
                stranded.append("twitter")
            elif source == "zhihu" and self.zhihu_producer is None:
                stranded.append("zhihu")
            elif source == "reddit" and self.reddit_producer is None:
                stranded.append("reddit")
            elif source == "bangumi" and self.bangumi_producer is None:
                stranded.append("bangumi")
            elif source == "linuxdo" and self.linuxdo_producer is None:
                stranded.append("linuxdo")
            elif source == "v2ex" and self.v2ex_producer is None:
                stranded.append("v2ex")
            elif source == "weibo" and self.weibo_producer is None:
                stranded.append("weibo")
            elif source not in {
                "bilibili",
                "xiaohongshu",
                "douyin",
                "youtube",
                "twitter",
                "zhihu",
                "reddit",
                "bangumi",
                "linuxdo",
                "v2ex",
                "weibo",
            }:
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
            min_eval_batch = self._candidate_eval_batch_floor()
            if min_eval_batch > 1:
                effective_limit = max(effective_limit, min_eval_batch)
        else:
            effective_limit = max(self.discovery_limit, requested_limit)
        return min(_MAX_DISCOVERY_BACKFILL_PER_REFRESH, max(1, effective_limit))

    def _candidate_eval_batch_floor(self) -> int:
        pipeline = self.discovery_candidate_pipeline
        if pipeline is None:
            return 1
        try:
            configured = int(getattr(pipeline, "min_eval_batch_size", 1) or 1)
        except (TypeError, ValueError):
            configured = 1
        return min(_MAX_DISCOVERY_BACKFILL_PER_REFRESH, max(1, configured))

    def _bounded_one_shot_inline_eval_limit(self, requested_limit: int) -> int:
        """Cap a non-daemon refresh's first supply/evaluation wave when configured.

        The API runtime has a live ``CandidateEvalCoordinator`` which can keep
        draining the raw queue after the HTTP response returns.  OpenClaw's
        short-lived adapter has no such owner, so its bootstrap opts into a
        small first wave that can reach durable copy within the adapter timeout.
        """

        requested = max(1, int(requested_limit))
        if self.candidate_eval_coordinator is not None:
            return requested
        try:
            configured = int(self.one_shot_inline_eval_limit)
        except (TypeError, ValueError):
            configured = 0
        if configured <= 0:
            return requested
        return min(requested, configured)

    def _candidate_eval_drain_batch_size(self, batch_size: int | None) -> int:
        default = min(
            _MAX_DISCOVERY_BACKFILL_PER_REFRESH,
            max(self.discovery_limit, _DEFAULT_CANDIDATE_EVAL_BATCH_SIZE),
        )
        if batch_size is None:
            return default
        try:
            requested = int(batch_size)
        except (TypeError, ValueError):
            return default
        if requested <= 0:
            return default
        return requested

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
        requested_budget = max(1, int(requested_limit))
        if pool_below_target:
            min_eval_batch = self._candidate_eval_batch_floor()
            total_gap = max(total_gap, min_eval_batch)
            requested_budget = max(requested_budget, min_eval_batch)
        shared_budget = min(
            requested_budget,
            max(1, int(effective_limit)),
            total_gap,
        )
        if set(strategies) == set(_BILIBILI_DISCOVERY_SOURCES) and (
            self._should_defer_expensive_bilibili_strategies(total_gap)
        ):
            cheap = ["search", "related_chain"]
            cheap_limits = self._split_budget_across_strategies(cheap, shared_budget)
            return {strategy: cheap_limits.get(strategy, 0) for strategy in strategies}
        return self._split_budget_across_strategies(strategies, shared_budget)

    def _should_defer_expensive_bilibili_strategies(self, total_gap: int) -> bool:
        threshold = max(
            _BILIBILI_EXPENSIVE_DISCOVERY_MIN_GAP,
            int(self.pool_target_count * _BILIBILI_EXPENSIVE_DISCOVERY_GAP_RATIO),
        )
        return int(total_gap) < threshold

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

    def _is_due(self, value: str, *, minutes: int) -> bool:
        if minutes <= 0:
            return True
        last_run = self._parse_iso_datetime(value)
        if last_run is None:
            return True
        return self._now() - last_run >= timedelta(minutes=minutes)

    def _is_due_soon(self, value: str, *, minutes: int, lead_seconds: int) -> bool:
        if minutes <= 0:
            return True
        last_run = self._parse_iso_datetime(value)
        if last_run is None:
            return True
        due_at = last_run + timedelta(minutes=minutes)
        return self._now() >= due_at - timedelta(seconds=max(0, int(lead_seconds)))

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
