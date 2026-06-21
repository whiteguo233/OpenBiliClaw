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
from typing import Any

from openbiliclaw.discovery.douyin import DouyinDiscoveryOptions, DouyinDiscoveryResult
from openbiliclaw.runtime.keyword_fetch import PLATFORM_DOUYIN as _PLATFORM_DOUYIN
from openbiliclaw.sources.douyin_plugin_search import (
    DouyinBudgetExhausted as _DouyinBudgetExhausted,
)

logger = logging.getLogger(__name__)

DouyinDiscoverCallable = Callable[[Any, DouyinDiscoveryOptions], Awaitable[DouyinDiscoveryResult]]
_DOUYIN_SCORE_THRESHOLDS = {
    "search": 0.60,
    "hot": 0.60,
    "feed": 0.60,
}
_DOUYIN_DEFAULT_SCORE_THRESHOLD = _DOUYIN_SCORE_THRESHOLDS["search"]


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
    min_interval_minutes: int = 30
    sources: tuple[str, ...] = ("search", "hot", "feed")
    evaluate: bool = True
    candidate_pipeline: Any | None = None
    per_source_limit: int = 20
    # Unified keyword planner fetch coordinator (P1.7). When wired AND the flag
    # is on, the producer's search source claims words from the keyword store
    # and walks them through the inline-admit lifecycle (used / failed /
    # budget-rollback). ``None`` (default / tests / flag off) → legacy path.
    keyword_fetch: Any | None = None
    _last_run_at: datetime | None = field(default=None, init=False)
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        """Run one Douyin discovery cycle if enabled and due."""
        if not self.enabled:
            return self._skip("disabled")
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
        per_source_limit = max(
            1,
            min(
                self.per_source_limit,
                math.ceil(requested_limit / max(1, len(selected_sources))),
            ),
        )
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
            claimed = coordinator.claim(_PLATFORM_DOUYIN)
            if not claimed:
                # Flag on but the store has no claimable pending words → skip the
                # search fetch this cycle (the planner will refill); don't run a
                # legacy self-generated search behind the planner's back.
                return self._skip("no_keywords")

        options = DouyinDiscoveryOptions(
            limit=requested_limit,
            sources=selected_sources,
            cache=not use_candidate_pipeline,
            evaluate=False if use_candidate_pipeline else self.evaluate,
            per_source_limit=per_source_limit,
            keywords_per_run=1,
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
            return self._skip("budget_exhausted")
        except Exception as exc:
            logger.warning("douyin producer failed: %s", exc)
            if claimed and coordinator is not None:
                coordinator.mark_failed(claimed)
            return self._skip("error")

        # Inline-admit lifecycle: a successful return that produced candidates
        # marks every claimed word ``used``; an empty fetch marks them ``failed``
        # (retry). yield backfill is P1.8, decoupled from ``used``.
        if claimed and coordinator is not None:
            if result.items:
                coordinator.mark_used(claimed)
            else:
                coordinator.mark_failed(claimed)

        self._last_run_at = datetime.now(UTC)
        payload: dict[str, object] = {
            "discovered": len(result.items),
            "source_counts": dict(result.source_counts),
            "reason": "ok",
        }
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
        if enqueued > 0:
            drain_result = await self.candidate_pipeline.drain_pending(
                profile=profile,
                batch_size=requested_limit,
            )
            payload.update(drain_result)
        return payload

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        if self._last_run_at is None:
            return True
        return datetime.now(UTC) - self._last_run_at >= timedelta(minutes=self.min_interval_minutes)

    def _sources_for_limit(self, requested_limit: int) -> tuple[str, ...]:
        configured = tuple(source for source in self.sources if str(source).strip())
        if requested_limit >= 10:
            selected = tuple(source for source in ("search", "hot") if source in configured)
            if selected:
                return selected
            return configured[:1] or ("search",)

        preferred = ("feed",) if requested_limit <= 3 else ("hot", "feed")
        selected = tuple(source for source in preferred if source in configured)
        if selected:
            return selected

        non_search = tuple(source for source in configured if source != "search")
        if non_search:
            return non_search[:1]
        return configured[:1] or ("search",)

    def _candidate_pool_full(self) -> bool:
        if self.candidate_pipeline is None:
            return False
        pool_full = getattr(self.candidate_pipeline, "pool_full", None)
        if not callable(pool_full):
            return False
        try:
            return bool(pool_full())
        except Exception:
            logger.debug("douyin producer: candidate pool fullness unavailable", exc_info=True)
            return False

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
                )
            service = DouyinDiscoveryService(
                client=client,
                discovery_engine=discovery_engine,
            )
            return await service.discover(profile, options)

    scheduler = getattr(config, "scheduler", None)
    return DouyinDiscoveryProducer(
        soul_engine=soul_engine,
        discover=_discover,
        enabled=bool(getattr(scheduler, "enabled", True)),
        min_interval_minutes=30,
        sources=("search", "hot", "feed"),
        candidate_pipeline=candidate_pipeline,
        per_source_limit=20,
        keyword_fetch=keyword_fetch,
    )
