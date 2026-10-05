"""Runtime Douyin discovery producer.

The continuous refresh controller owns pool quotas. This producer owns
the throttled call into the reusable Douyin discovery service when the
Douyin platform family is under quota.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from openbiliclaw.config import publication_date_preference_for_source
from openbiliclaw.discovery.douyin import DouyinDiscoveryOptions, DouyinDiscoveryResult
from openbiliclaw.runtime.keyword_fetch import PLATFORM_DOUYIN as _PLATFORM_DOUYIN
from openbiliclaw.runtime.pool_gate import candidate_pool_full_for_source
from openbiliclaw.runtime.producer_cadence import (
    ledger_available,
    producer_ran_within,
    record_producer_run,
)
from openbiliclaw.sources.douyin_plugin_search import (
    DouyinBudgetExhausted as _DouyinBudgetExhausted,
)

if TYPE_CHECKING:
    from openbiliclaw.recommendation.publication_preference import PublicationDatePreference

logger = logging.getLogger(__name__)

DouyinDiscoverCallable = Callable[[Any, DouyinDiscoveryOptions], Awaitable[DouyinDiscoveryResult]]
_DOUYIN_SCORE_THRESHOLDS = {
    "search": 0.60,
    "hot": 0.60,
    "feed": 0.60,
}
_DOUYIN_DEFAULT_SCORE_THRESHOLD = _DOUYIN_SCORE_THRESHOLDS["search"]
# Calibrated from the 2026-08-09 real-extension E2E: successful tasks took
# roughly 15-35s while an execution-context loss consumed the full 180s task
# watchdog. These are engineering retry floors, not Douyin limits: 15m avoids
# colliding with the durable stale-lease window and 60m prevents an exhausted
# daily budget from being polled every refresh tick. Recalibrate if the task
# watchdog, lease duration, or producer cadence changes.
_DOUYIN_FAILURE_RETRY_MINUTES = 15
_DOUYIN_BUDGET_RETRY_MINUTES = 60


def douyin_runtime_hot_budget(*, base_budget: int, requested_limit: int) -> int:
    """Return the effective hot-task budget for one runtime replenishment run."""
    configured = int(base_budget)
    if configured <= 0:
        return 0
    requested = max(1, int(requested_limit))
    if requested < 10:
        return configured
    return max(configured, min(60, requested))


@dataclass
class DouyinDiscoveryProducer:
    """Throttle and invoke Douyin discovery from the runtime loop."""

    soul_engine: Any
    discover: DouyinDiscoverCallable
    enabled: bool = True
    min_interval_minutes: int = 3
    sources: tuple[str, ...] = ("search", "hot", "feed")
    # How many pending keywords to claim AND search per run. Must equal the
    # strategy's effective search count: the strategy truncates seed keywords to
    # ``keywords_per_run`` (douyin_direct._dedupe_cap), so claiming more than we
    # search silently burns the extra words as ``used`` without ever searching
    # them. Keep this in lock-step with ``DouyinDiscoveryOptions.keywords_per_run``.
    keywords_per_run: int = 3
    evaluate: bool = True
    candidate_pipeline: Any | None = None
    # API/OpenClaw runtime composition flips this after attaching its shared
    # CandidateEvalCoordinator. Standalone producer runs preserve the legacy
    # inline drain path.
    candidate_evaluation_owned_by_coordinator: bool = False
    per_source_limit: int = 20
    date_preference: PublicationDatePreference | None = None
    # Unified keyword planner fetch coordinator (P1.7). When wired AND the flag
    # is on, the producer's search source claims words from the keyword store
    # and walks each word through its own used / failed / transient-requeue /
    # budget-rollback lifecycle. ``None`` (default / tests / flag off) → legacy path.
    keyword_fetch: Any | None = None
    # Only used for the restart-surviving cadence floor; None falls back to
    # the in-process stamp.
    database: Any | None = None
    # API daemon-only browser availability gate. Explicit CLI/debug producers
    # leave this None so they can still run a deliberate smoke request.
    presence: Any | None = None
    presence_grace_seconds: int = 90
    _last_run_at: datetime | None = field(default=None, init=False)
    _retry_not_before: datetime | None = field(default=None, init=False)
    _last_skip_reason: str = field(default="", init=False)
    _non_search_rotation: int = field(default=0, init=False)

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        """Run one Douyin discovery cycle if enabled and due."""
        if not self.enabled:
            return self._skip("disabled")
        if not self._extension_present():
            return self._skip("extension_absent")
        if not self._is_due():
            return self._skip("throttled")
        if self._candidate_pool_full():
            return self._skip("pool_full")

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("douyin producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        requested_limit = max(1, int(limit or self.per_source_limit))
        selected_sources = self._sources_for_limit(requested_limit)
        use_candidate_pipeline = self.candidate_pipeline is not None

        # Unified keyword planner fetch path (P1.7, flag-gated). Only when this
        # run actually includes the ``search`` source — hot/feed-only runs never
        # touch the keyword store. The deficit gate is enforced upstream (the
        # controller only invokes the producer when douyin is under quota); the
        # distinct floor is ``min_interval`` via ``_is_due`` above.
        claimed: list[Any] = []
        coordinator = self.keyword_fetch
        flag_on_search = (
            coordinator is not None
            and bool(getattr(coordinator, "should_claim", lambda: False)())
            and "search" in selected_sources
        )
        if flag_on_search and coordinator is not None:
            # Claim exactly as many words as the strategy will search
            # (``keywords_per_run``) so none are burned unsearched.
            claimed = coordinator.claim(_PLATFORM_DOUYIN, n=self.keywords_per_run)
            # An empty unified store falls back to the strategy's profile-derived
            # keywords. Independent hot/feed branches must keep running either way.

        per_source_limit = max(
            1,
            min(
                self.per_source_limit,
                math.ceil(requested_limit / max(1, len(selected_sources))),
            ),
        )

        options = DouyinDiscoveryOptions(
            limit=requested_limit,
            sources=selected_sources,
            cache=not use_candidate_pipeline,
            evaluate=False if use_candidate_pipeline else self.evaluate,
            per_source_limit=per_source_limit,
            keywords_per_run=self.keywords_per_run,
            date_preference=self.date_preference,
            keywords=tuple(item.keyword for item in claimed) if claimed else (),
            # P1.8: thread the producing word's id onto each search candidate for
            # admit-time yield backfill.
            keyword_ids={item.keyword: int(item.id) for item in claimed} if claimed else {},
            raise_on_budget=bool(claimed),
        )
        try:
            result = await self.discover(profile, options)
        except _DouyinBudgetExhausted:
            # Claimed but the plugin search budget was spent → no search ran →
            # roll every claimed word back to pending (do NOT burn as used).
            if coordinator is not None:
                for item in claimed:
                    coordinator.rollback(item)
            self._stamp_run(0, reason="budget_exhausted")
            return self._skip("budget_exhausted")
        except Exception as exc:
            logger.warning("douyin producer failed: %s", exc)
            if claimed and coordinator is not None:
                self._requeue_claimed_transient(coordinator, claimed)
            self._stamp_run(0, reason="error")
            return self._skip("error")

        if claimed and coordinator is not None:
            self._finalize_claimed_keywords(
                coordinator,
                claimed,
                result.keyword_outcomes,
            )

        result_reason = self._result_reason(result)
        self._stamp_run(len(result.items), reason=result_reason)
        payload: dict[str, object] = {
            "discovered": len(result.items),
            "source_counts": dict(result.source_counts),
            "reason": result_reason,
        }
        if result.source_outcomes:
            payload["source_outcomes"] = dict(result.source_outcomes)
        if result.keyword_outcomes:
            payload["keyword_outcomes"] = dict(result.keyword_outcomes)
        if self.candidate_pipeline is None:
            payload["cached"] = result.cached
            return payload

        self._stamp_candidate_score_thresholds(result.items)
        enqueued = int(
            self.candidate_pipeline.enqueue_candidates(
                list(result.items),
                source_context="douyin",
            )
        )
        payload["enqueued"] = enqueued
        if enqueued > 0 and not self.candidate_evaluation_owned_by_coordinator:
            drain_result = await self.candidate_pipeline.drain_pending(
                profile=profile,
                batch_size=requested_limit,
            )
            payload.update(drain_result)
        return payload

    def _stamp_run(self, discovered: int, *, reason: str = "ok") -> None:
        """Record this attempt and apply the plugin-specific retry floor.

        Productive rounds go to the shared ledger so the floor survives a
        restart. Every attempt also gets an in-process stamp: the shared ledger
        deliberately ignores empty rounds, but immediately retrying a browser
        task can create an unbounded pending-task loop when the extension is
        offline. Infrastructure failures receive a longer cool-down than a
        genuine empty response.
        """
        now = datetime.now(UTC)
        record_producer_run(getattr(self, "database", None), "douyin", int(discovered))
        self._last_run_at = now
        if int(discovered) > 0 or reason in {"ok", "empty"}:
            self._retry_not_before = None
        elif reason == "budget_exhausted":
            self._retry_not_before = now + timedelta(minutes=_DOUYIN_BUDGET_RETRY_MINUTES)
        elif reason in {"error", "timeout"}:
            self._retry_not_before = now + timedelta(minutes=_DOUYIN_FAILURE_RETRY_MINUTES)

    def _is_due(self) -> bool:
        now = datetime.now(UTC)
        if self._retry_not_before is not None and now < self._retry_not_before:
            return False
        if self.min_interval_minutes <= 0:
            return True
        # The persisted ledger is intentionally productive-only. Keep this
        # local attempt floor as well so empty plugin tasks cannot be enqueued
        # once per refresh tick for the lifetime of the backend process.
        if self._last_run_at is not None and now - self._last_run_at < timedelta(
            minutes=self.min_interval_minutes
        ):
            return False
        database = getattr(self, "database", None)
        if ledger_available(database):
            # Restart-surviving floor keyed on the last *productive* round.
            return not producer_ran_within(database, "douyin", self.min_interval_minutes)
        if self._last_run_at is None:
            return True
        return now - self._last_run_at >= timedelta(minutes=self.min_interval_minutes)

    def _extension_present(self) -> bool:
        """Fail fast in daemon mode when no extension can claim browser tasks.

        ``presence=None`` preserves explicit CLI/debug construction. The
        runtime factory injects the shared presence tracker because all three
        steady-state sources currently use the browser task bridge.
        """
        if self.presence is None:
            return True
        is_present = getattr(self.presence, "is_present", None)
        if not callable(is_present):
            return False
        try:
            return bool(is_present(max(1, int(self.presence_grace_seconds))))
        except Exception:
            logger.debug("douyin producer: extension presence unavailable", exc_info=True)
            return False

    def _sources_for_limit(self, requested_limit: int) -> tuple[str, ...]:
        configured = tuple(source for source in self.sources if str(source).strip())
        if requested_limit >= 10:
            selected: list[str] = []
            if "search" in configured:
                selected.append("search")
            non_search = tuple(source for source in ("hot", "feed") if source in configured)
            if non_search:
                selected.append(non_search[self._non_search_rotation % len(non_search)])
                self._non_search_rotation += 1
            if selected:
                return tuple(selected)
            return configured[:1] or ("search",)

        preferred: tuple[str, ...] = ("feed",) if requested_limit <= 3 else ("hot", "feed")
        preferred_configured = tuple(source for source in preferred if source in configured)
        if preferred_configured:
            return preferred_configured

        configured_non_search = tuple(source for source in configured if source != "search")
        if configured_non_search:
            return configured_non_search[:1]
        return configured[:1] or ("search",)

    def _candidate_pool_full(self) -> bool:
        return candidate_pool_full_for_source(
            self.candidate_pipeline, "douyin", logger=logger, label="douyin producer"
        )

    @staticmethod
    def _finalize_claimed_keywords(
        coordinator: Any,
        claimed: list[Any],
        outcomes: dict[str, str],
    ) -> None:
        for item in claimed:
            outcome = str(outcomes.get(str(item.keyword), "") or "").strip().lower()
            if outcome == "used":
                coordinator.mark_used([item])
            elif outcome == "empty":
                coordinator.mark_failed([item])
            elif outcome in {"timeout", "failed"}:
                requeue = getattr(coordinator, "requeue_transient", None)
                if callable(requeue):
                    requeue(item)
                else:
                    coordinator.rollback(item)
            else:
                # Budget rejection and missing outcome both mean the word was not
                # proven delivered. Keep it claimable instead of burning it.
                coordinator.rollback(item)

    @staticmethod
    def _requeue_claimed_transient(coordinator: Any, claimed: list[Any]) -> None:
        requeue = getattr(coordinator, "requeue_transient", None)
        for item in claimed:
            if callable(requeue):
                requeue(item)
            else:
                coordinator.rollback(item)

    @staticmethod
    def _result_reason(result: DouyinDiscoveryResult) -> str:
        if result.items:
            return "ok"
        outcomes = set(result.source_outcomes.values())
        if outcomes and outcomes <= {"budget_exhausted"}:
            return "budget_exhausted"
        if "timeout" in outcomes:
            return "timeout"
        if "failed" in outcomes:
            return "error"
        return "empty"

    def _stamp_candidate_score_thresholds(self, items: list[Any]) -> None:
        for item in items:
            try:
                if float(getattr(item, "score_threshold", 0.0) or 0.0) > 0:
                    continue
                item.score_threshold = self._score_threshold_for_item(item)
            except Exception:
                logger.debug("douyin producer: failed to stamp score threshold", exc_info=True)

    @staticmethod
    def _score_threshold_for_item(item: Any) -> float:
        strategy = str(getattr(item, "source_strategy", "") or "").strip().lower()
        for key, threshold in _DOUYIN_SCORE_THRESHOLDS.items():
            if key in strategy:
                return threshold
        return _DOUYIN_DEFAULT_SCORE_THRESHOLD

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("douyin producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"discovered": 0, "reason": reason}


def build_douyin_discovery_producer(
    *,
    config: Any,
    database: Any,
    soul_engine: Any,
    discovery_engine: Any,
    candidate_pipeline: Any | None = None,
    keyword_fetch: Any | None = None,
    presence: Any | None = None,
    presence_grace_seconds: int = 90,
    enabled_override: bool | None = None,
) -> DouyinDiscoveryProducer | None:
    """Build the runtime Douyin producer if Douyin discovery is enabled."""
    dy_cfg = getattr(getattr(config, "sources", None), "douyin", None)
    if dy_cfg is None or not bool(getattr(dy_cfg, "enabled", False)):
        return None
    if str(getattr(dy_cfg, "mode", "direct")).strip().lower() != "direct":
        logger.info("douyin producer disabled: unsupported mode=%r", getattr(dy_cfg, "mode", ""))
        return None
    if not hasattr(database, "conn"):
        logger.info("douyin producer disabled: database does not expose task tables")
        return None

    async def _discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        from openbiliclaw.discovery.douyin import DouyinDiscoveryService
        from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie
        from openbiliclaw.sources.douyin_direct import DouyinDirectClient
        from openbiliclaw.sources.douyin_plugin_search import DouyinPluginSearchClient

        cookie_env = str(getattr(dy_cfg, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE"))
        cookie = resolve_douyin_cookie(
            data_dir=config.data_path,
            cookie_env=cookie_env,
        )
        if not cookie:
            raise RuntimeError(
                f"missing Douyin cookie; set {cookie_env} or keep the browser extension online"
            )

        async with DouyinDirectClient(cookie=cookie) as direct_client:
            client: Any = direct_client
            if any(source in options.sources for source in ("search", "hot", "feed")):
                wait_seconds = float(
                    os.environ.get("OPENBILICLAW_DY_DISCOVERY_SEARCH_WAIT_SECONDS", "180")
                )
                client = DouyinPluginSearchClient(
                    database=database,
                    direct_client=direct_client,
                    wait_seconds=wait_seconds,
                    daily_search_budget=int(getattr(dy_cfg, "daily_search_budget", 0)),
                    daily_hot_budget=douyin_runtime_hot_budget(
                        base_budget=int(getattr(dy_cfg, "daily_hot_budget", 0)),
                        requested_limit=options.limit,
                    ),
                    daily_feed_budget=int(getattr(dy_cfg, "daily_feed_budget", 0)),
                    # Unified keyword planner fetch path: surface budget
                    # exhaustion as a distinguishable signal so the claimed
                    # keyword rolls back instead of being burned (P1.7).
                    raise_on_budget=bool(getattr(options, "raise_on_budget", False)),
                    # The runtime config is ``mode = "direct"`` and a cookie is
                    # required to get here, so when the browser-plugin task
                    # returns empty/failed (extension permission missing,
                    # Douyin risk-control page, etc.) fall back to the direct
                    # cookie client instead of producing zero candidates.
                    allow_direct_fallback=True,
                )
            service = DouyinDiscoveryService(
                client=client,
                discovery_engine=discovery_engine,
            )
            return await service.discover(profile, options)

    scheduler = getattr(config, "scheduler", None)
    dy_cfg = getattr(getattr(config, "sources", None), "douyin", None)
    producer_enabled = (
        bool(getattr(scheduler, "enabled", True))
        if enabled_override is None
        else bool(enabled_override)
    )
    return DouyinDiscoveryProducer(
        soul_engine=soul_engine,
        discover=_discover,
        enabled=producer_enabled,
        presence=presence,
        presence_grace_seconds=presence_grace_seconds,
        min_interval_minutes=int(getattr(dy_cfg, "min_interval_minutes", 3)),
        database=database,
        sources=("search", "hot", "feed"),
        candidate_pipeline=candidate_pipeline,
        per_source_limit=20,
        keyword_fetch=keyword_fetch,
        date_preference=publication_date_preference_for_source(dy_cfg),
    )
