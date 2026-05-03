"""Continuous refresh controller for the local API runtime."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD
from openbiliclaw.soul.speculator import build_probe_axis, choose_next_probe_candidate

logger = logging.getLogger(__name__)

_MAX_DISCOVERY_BACKFILL_PER_REFRESH = 60
_SOURCE_TARGET_SHARES: tuple[tuple[str, int], ...] = (
    ("search", 4),
    ("related_chain", 4),
    # trending was 3 (target=120 at pool_target=600). Empirically trending
    # never reaches that floor: items have higher consumption rate than
    # other sources (relevance is high, popup serves them quickly) and
    # the topic_group cap suppresses repeat 鬼畜/同人/短片 surfaces. The
    # observed steady-state is ~30-45, so the deficit logic kept firing
    # solo trending rounds every 60s that net only ~1 fresh item — pure
    # LLM-evaluation waste. Drop share to 1 (target ~46) to align with
    # reality.
    ("trending", 1),
    ("explore", 4),
    # Xiaohongshu has multiple internal extension/task channels, but the
    # runtime pool should treat them as one first-class source family.
    # The xhs_producer owns replenishment; Bilibili discover() plans below
    # only fill deficits for the four Bilibili strategies.
    ("xiaohongshu", 4),
)
_BILIBILI_DISCOVERY_SOURCES = ("search", "related_chain", "trending", "explore")


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
    def count_pool_candidates(self) -> int: ...
    def count_pool_candidates_by_source(self) -> dict[str, int]: ...
    def trim_explore_cluster_overflow(self, *, max_per_cluster: int = 3) -> int: ...
    def trim_topic_group_overflow(self, *, max_per_group: int) -> int: ...
    def trim_pool_to_target_count(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int] | None = None,
    ) -> int: ...
    def reactivate_under_quota_pool_sources(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int],
    ) -> int: ...
    def evict_stale_pool_items(self, *, max_age_days: int = 14) -> int: ...
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


@dataclass
class ContinuousRefreshController:
    """Keep discovery cache and recommendations fresh during API runtime."""

    memory_manager: SupportsRuntimeState
    database: SupportsEventDatabase
    soul_engine: SupportsProfileEngine
    discovery_engine: SupportsDiscoveryEngine
    recommendation_engine: SupportsRecommendationEngine
    event_hub: Any | None = None
    xhs_producer: Any | None = None
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
    pool_target_count: int = 600
    _manual_refresh_task: asyncio.Task[None] | None = None
    _manual_refresh_state: str = "idle"
    _manual_refresh_message: str = ""
    _manual_refresh_started_at: str = ""
    _manual_refresh_finished_at: str = ""
    # Last-tick fingerprint of pool maintenance state, used to demote
    # the per-minute "reactivated=N" / "trim dropped=N top=X" log lines
    # to DEBUG when nothing actually changed since the previous tick.
    # INFO fires only when the count or top-group rotates.
    _last_pool_maintenance_fingerprint: tuple[int, int, str] = (-1, -1, "")
    # Last pool_available count emitted via the runtime event stream so
    # popup-side ``mergeRuntimeStatusEvent`` only re-renders when the
    # number actually changes — see ``_publish_pool_status_if_changed``.
    _last_published_pool_count: int = -1

    _signal_event_types = [
        "view",
        "search",
        "favorite",
        "like",
        "coin",
        "comment",
        "feedback",
    ]

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
        return {
            "initialized": self._is_initialized(),
            "recommendation_count": self.database.count_recommendations(),
            "pending_signal_events": self._pending_signal_events_count(state),
            "last_refresh_at": last_refresh_at,
            "last_notification_at": str(state.get("last_notification_at", "")),
            "unread_count": self.database.count_unread_recommendations(),
            "pool_available_count": self.database.count_pool_candidates(),
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
        """Refresh discovery candidates when thresholds are met."""
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

    async def force_refresh(self) -> dict[str, object]:
        """Run a full refresh immediately, bypassing runtime thresholds.

        Runs all 4 Bilibili strategies in a single discover() call so they
        execute concurrently via asyncio.gather, maximizing pool diversity. The pool
        target still applies as a hard cap — if the pool is already full, no
        discovery runs and overflow is trimmed.
        """
        state = self.memory_manager.load_discovery_runtime_state()
        if not self._is_initialized():
            return {"refreshed": False, "strategies": [], "reason": "not_initialized"}

        pool_at_cap = self._enforce_pool_cap()
        await self._publish_pool_status_if_changed()
        if pool_at_cap:
            return {"refreshed": False, "strategies": [], "reason": "pool_at_cap"}

        profile = await self.soul_engine.get_profile()
        plan = [
            (["search", "trending"], self.discovery_limit),
            (["related_chain", "explore"], self.discovery_limit),
        ]
        return await self._run_refresh_plan(
            state=state,
            profile=profile,
            plan=plan,
            reason="manual",
        )

    def _enforce_pool_cap(self) -> bool:
        """Trim any overflow and report whether the pool is at/above target.

        Returns ``True`` when the pool sits at or above ``pool_target_count``
        *after* trimming, signalling the caller to skip discovery. A return of
        ``False`` means there is room to replenish.
        """
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

        source_targets = self._source_target_counts()
        reactivate_fn = getattr(self.database, "reactivate_under_quota_pool_sources", None)
        if callable(reactivate_fn):
            try:
                reactivated = reactivate_fn(
                    target=self.pool_target_count,
                    source_share_quotas=source_targets,
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

        pool_available = self.database.count_pool_candidates()
        if pool_available > self.pool_target_count:
            trimmed = 0
            try:
                trimmed = self.database.trim_pool_to_target_count(
                    target=self.pool_target_count,
                    source_share_quotas=source_targets,
                )
            except Exception:
                logger.exception("trim_pool_to_target_count failed")
            pool_available = self.database.count_pool_candidates()
            logger.info(
                "enforce_pool_cap: trimmed=%s, pool_available=%s, target=%s",
                trimmed,
                pool_available,
                self.pool_target_count,
            )
        else:
            logger.debug(
                "enforce_pool_cap: no trim needed, pool_available=%s, target=%s",
                pool_available,
                self.pool_target_count,
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
        self._manual_refresh_task = asyncio.create_task(self._complete_manual_refresh())
        return {"accepted": True, "state": "running", "reason": "started"}

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
        }

    def _load_disliked_topic_phrases(self) -> list[str]:
        """Return lowercased disliked-topic substrings from the preference layer.

        Returns an empty list if the layer is missing or the field is
        unset.  Phrases are used as case-insensitive substring matches
        against title + tags, so generic entries like '低质内容' won't
        match anything concrete (which is fine — they're meant for the
        evaluator, not the proactive push filter).
        """
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

    async def run_forever(self) -> None:
        """Launch all background tasks as independent concurrent loops.

        Each task runs on its own timer so a slow discovery refresh
        (10+ minutes when B站 API challenges every request) never
        blocks proactive notifications, soul pipeline ticks, or XHS
        keyword production.

        Architecture::

            ┌─ _loop_refresh()       60s   LLM-heavy, may take minutes
            ├─ _loop_soul_pipeline()  60s   profile updates, speculator
            ├─ _loop_xhs_producer()   60s   keyword generation
            └─ _loop_proactive_push() 60s   delight + interest probe
        """
        with suppress(Exception):
            await self.prepare_delight_candidates()
        tasks = [
            asyncio.create_task(self._loop_refresh()),
            asyncio.create_task(self._loop_soul_pipeline()),
            asyncio.create_task(self._loop_xhs_producer()),
            asyncio.create_task(self._loop_proactive_push()),
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
            with suppress(Exception):
                await self.refresh_if_needed()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_soul_pipeline(self) -> None:
        """Soul profile pipeline — buffer flushes, speculator, cognition."""
        while True:
            with suppress(Exception):
                await self._tick_soul_pipeline()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_xhs_producer(self) -> None:
        """XHS keyword production — Soul-driven search task generation."""
        while True:
            with suppress(Exception):
                await self._tick_xhs_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_proactive_push(self) -> None:
        """Delight + interest probe push — lightweight, never blocks.

        Runs on a longer cadence than the main refresh loop because
        probes/delight are not streaming content — once the active set
        has been delivered, additional pushes within minutes only
        contribute notification fatigue.
        """
        while True:
            with suppress(Exception):
                await self._publish_delight_if_available()
            with suppress(Exception):
                await self._publish_interest_probe_if_available()
            await asyncio.sleep(self.proactive_push_interval_seconds)

    async def _tick_xhs_producer(self) -> None:
        """Invoke the xhs search task producer if one is configured."""
        producer = self.xhs_producer
        if producer is None:
            return
        produce_fn = getattr(producer, "produce_if_due", None)
        if not callable(produce_fn):
            return
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
        pool_available = self.database.count_pool_candidates()
        pool_below_target = pool_available < self.pool_target_count

        if pool_below_target:
            source_plan = self._build_source_replenishment_plan()
            if source_plan:
                return source_plan
            return [
                (["search", "trending"], self.discovery_limit),
                (["related_chain", "explore"], self.discovery_limit),
            ]

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

    async def _complete_manual_refresh(self) -> None:
        try:
            await self.force_refresh()
        except Exception as exc:
            self._manual_refresh_state = "failed"
            self._manual_refresh_message = f"这次补货没跑通：{exc}"
            self._manual_refresh_finished_at = self._now().isoformat()
            await self._publish_event(
                {
                    "type": "refresh.failed",
                    "phase": "failed",
                    "message": self._manual_refresh_message,
                    "pool_available_count": self.database.count_pool_candidates(),
                }
            )
            return
        self._manual_refresh_state = "success"
        runtime_state = self.memory_manager.load_discovery_runtime_state()
        last_discovered = self._int_state_value(runtime_state, "last_discovered_count")
        last_replenished = self._int_state_value(runtime_state, "last_replenished_count")
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
                "pool_available_count": self.database.count_pool_candidates(),
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
        before_pool_count = self.database.count_pool_candidates()
        initial_pool_below_target = before_pool_count < self.pool_target_count
        all_discovered: list[Any] = []
        flattened_strategies: list[str] = []
        replenished_topics: list[str] = []

        await self._publish_event(
            {
                "type": "refresh.started",
                "phase": "running",
                "message": "开始给你补候选了",
                "pool_available_count": before_pool_count,
            }
        )

        for strategies, requested_limit in plan:
            current_pool_count = self.database.count_pool_candidates()
            if current_pool_count >= self.pool_target_count:
                break

            await self._publish_event(
                {
                    "type": "refresh.strategy",
                    "phase": "running",
                    "strategy": "+".join(strategies),
                    "message": self._strategy_message(strategies),
                    "pool_available_count": current_pool_count,
                }
            )

            discovered = await self.discovery_engine.discover(
                profile,
                strategies=strategies,
                limit=self._requested_refresh_limit(
                    requested_limit=requested_limit,
                    current_pool_count=current_pool_count,
                    pool_below_target=initial_pool_below_target,
                ),
            )
            all_discovered.extend(discovered)
            flattened_strategies.extend(strategies)

            if discovered:
                replenished_topics.extend(self._extract_topics(discovered))

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
            await self.recommendation_engine.precompute_pool_copy(
                profile=profile,
                limit=_MAX_DISCOVERY_BACKFILL_PER_REFRESH,
            )
            # Pre-warm supergroup-merge embeddings so the popup's "换一批"
            # hot path always hits the L1/L2 cache. New labels added by
            # this refresh round get warmed before the user clicks.
            try:
                await self.recommendation_engine.prewarm_supergroup_embeddings()
            except Exception:
                logger.exception("prewarm_supergroup_embeddings failed")
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
            await self._publish_interest_probe_if_available()

        now = self._now().isoformat()
        latest_event_id = self.database.get_latest_event_id()
        if "search" in flattened_strategies or "related_chain" in flattened_strategies:
            state["last_event_refresh_at"] = now
            state["last_processed_event_id"] = latest_event_id
        if "trending" in flattened_strategies:
            state["last_trending_refresh_at"] = now
        if "explore" in flattened_strategies:
            state["last_explore_refresh_at"] = now
        after_pool_count = self.database.count_pool_candidates()
        state["last_discovered_count"] = len(all_discovered)
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
                "pool_available_count": after_pool_count,
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
        refresh tick completes (every 8h cron); now it stays in sync
        within seconds of any pool-state change.

        Only emits when the count is different from the last emit, so
        steady-state ticks don't spam the WebSocket stream.
        """
        try:
            current = int(self.database.count_pool_candidates())
        except Exception:
            return
        if current == self._last_published_pool_count:
            return
        self._last_published_pool_count = current
        await self._publish_event(
            {
                "type": "pool_status",
                "pool_available_count": current,
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

    async def _publish_event(self, event: dict[str, object]) -> None:
        publish = getattr(self.event_hub, "publish", None)
        if callable(publish):
            await publish(event)

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

    async def _publish_interest_probe_if_available(self) -> None:
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
            return
        specs = list(get_active())
        if not specs:
            return

        # Load probe history from runtime state
        state = self.memory_manager.load_discovery_runtime_state()
        probed: dict[str, str] = state.get("probed_domains", {})  # type: ignore[assignment]
        probed_axes: dict[str, str] = state.get("probed_axes", {})  # type: ignore[assignment]
        # Purge expired entries
        now = self._now()
        cutoff = (now - timedelta(hours=self._PROBE_COOLDOWN_HOURS)).isoformat()
        probed = {d: t for d, t in probed.items() if t > cutoff}
        probed_axes = {axis: t for axis, t in probed_axes.items() if t > cutoff}

        top = choose_next_probe_candidate(
            specs,
            probed_domains=set(probed),
            probed_axes=set(probed_axes),
        )
        if top is None:
            return  # All active specs were probed recently

        domain = str(getattr(top, "domain", "")).strip()
        if not domain:
            return

        # Record this probe
        probed[domain.lower()] = now.isoformat()
        state["probed_domains"] = probed
        axis = build_probe_axis(
            experience_mode=getattr(top, "experience_mode", ""),
            entry_load=getattr(top, "entry_load", ""),
        )
        if axis:
            probed_axes[axis] = now.isoformat()
        state["probed_axes"] = probed_axes
        self.memory_manager.save_discovery_runtime_state(state)
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
        await self._publish_event(
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
                "specifics": specifics,
                "question": question,
            }
        )

    def _strategy_message(self, strategies: list[str]) -> str:
        if strategies == ["search", "related_chain"]:
            return "先从你刚刚的口味里搜一轮"
        if strategies == ["trending"]:
            return "顺手看看站内热榜里有没有你会吃的"
        if strategies == ["explore"]:
            return "再给你探一点你可能会意外喜欢的"
        return "正在继续给你补候选"

    def _build_source_replenishment_plan(self) -> list[tuple[list[str], int]]:
        source_counts = self.database.count_pool_candidates_by_source()
        if not source_counts:
            return []

        target_counts = self._source_target_counts()
        deficits: list[tuple[str, int]] = []
        for source in _BILIBILI_DISCOVERY_SOURCES:
            deficit = max(0, target_counts[source] - int(source_counts.get(source, 0)))
            if deficit > 0:
                deficits.append((source, deficit))

        if not deficits:
            return []

        # Merge all deficient sources into a single discover() call so they
        # fan out in one asyncio.gather and get mixed by _compress_topic_repeats
        # with the per-source floor. Without this, each tick runs only one
        # source's plan entry, the pool overflows the cap, gets trimmed, and
        # the next tick runs the next source — turning each refresh round
        # into a single-source delivery for the user.
        merged_strategies = [source for source, _ in deficits]
        merged_limit = max(deficit for _, deficit in deficits)
        return [(merged_strategies, merged_limit)]

    def _source_target_counts(self) -> dict[str, int]:
        total_share = sum(share for _, share in _SOURCE_TARGET_SHARES)
        remaining = self.pool_target_count
        targets: dict[str, int] = {}
        for index, (source, share) in enumerate(_SOURCE_TARGET_SHARES):
            if index == len(_SOURCE_TARGET_SHARES) - 1:
                targets[source] = remaining
                break
            count = round(self.pool_target_count * share / total_share)
            count = min(remaining, count)
            targets[source] = count
            remaining -= count
        return targets

    def _requested_refresh_limit(
        self,
        *,
        requested_limit: int,
        current_pool_count: int,
        pool_below_target: bool,
    ) -> int:
        """Decide how many candidates each strategy should be asked for.

        v0.3.24+ pool-aware sizing. Pre-fix this enforced an absolute
        floor of ``discovery_limit`` (30) per strategy, even when the
        pool was 595/600 and only needed 5 more items. With 4 strategies
        × 30 = 120 candidates LLM-evaluated per refresh — and the
        suppress-pass keeping only ~20 — that meant ~80% of LLM
        evaluation cost went to candidates that were immediately
        suppressed. The fix sizes each strategy's limit to the actual
        pool gap (with 2x oversample for items below score threshold and
        a floor of 5 to keep strategies productive on tiny gaps),
        capped by ``discovery_limit`` so a sudden post-init replenish
        doesn't turn into a single huge wave.
        """
        if pool_below_target:
            gap = max(0, self.pool_target_count - current_pool_count)
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
