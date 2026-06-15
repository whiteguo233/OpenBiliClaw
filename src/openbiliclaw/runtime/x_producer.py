"""Runtime X (Twitter) discovery producer — fetch-only.

X steady-state discovery is server-side cookie replay (like Bilibili /
Douyin-direct). Once per throttle window this producer:

  1. Reads the current SoulProfile.
  2. Runs the three injected strategies through the :class:`XAdapter`:
     ``search`` (soul-driven keyword(s)), ``feed`` (For-You home timeline,
     throttled to a low daily cadence), and ``creator`` (each subscription
     due for a fetch via :class:`XCreatorStore`).
  3. Enqueues the resulting :class:`DiscoveredContent` into the
     ``discovery_candidates`` pending pool.

**Fetch-only contract (unified-pool spec).** The producer NEVER evaluates and
NEVER writes ``content_cache``. It only enqueues raw candidates; the shared
mixed-source evaluator (driven by the refresh loop's drain) owns scoring and
admission. There is no ``drain_pending`` call here.

**Lazy import.** The disabled path is a pure no-op and imports nothing from
``twitter_cli`` — the injected ``XAdapter`` / ``XClient`` own the lazy import
on their own network seam, and this module never references them at load time.

**Source health (spec §7).** Before each cycle the producer consults the
persisted :class:`XSourceHealthStore`: it skips entirely while a re-login
state (``missing_cookie`` / ``expired_cookie`` / ``blocked``) or an unexpired
rate-limit cooldown is active, and it skips the (high-visibility) For-You feed
once repeated For-You failures have auto-paused it. Every strategy run records
success / error so the state machine and the status API stay current.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
from openbiliclaw.runtime.keyword_fetch import PLATFORM_TWITTER as _PLATFORM_TWITTER

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.sources.x_tasks import XCreatorStore
    from openbiliclaw.storage.x_health import XSourceHealthStore

logger = logging.getLogger(__name__)

# The three server-side X discovery strategies. The adapter dispatches on
# ``recipe.strategy``; these names match XAdapter.fetch.
SEARCH = "search"
FEED = "feed"
CREATOR = "creator"
X_DISCOVERY_STRATEGIES = (SEARCH, FEED, CREATOR)

_X_SCORE_THRESHOLDS = {
    SEARCH: 0.62,
    FEED: 0.60,
    CREATOR: 0.62,
}


@dataclass
class XDiscoveryProducer:
    """Throttle and invoke X discovery from the runtime loop (fetch-only)."""

    database: Any
    soul_engine: Any
    adapter: Any  # XAdapter (structural: .fetch(recipe, profile, limit), .source_type)
    creator_store: XCreatorStore
    health_store: XSourceHealthStore
    enabled: bool = True
    min_interval_minutes: int = 60
    daily_search_budget: int = 0
    daily_feed_budget: int = 0
    daily_creator_budget: int = 0
    request_interval_seconds: int = 3
    creator_refresh_hours: int = 24
    # Optional — only used to detect a candidate-pool full state. NEVER used to
    # evaluate or admit (fetch-only); accessing an evaluator method would be a
    # bug, which ``tests/test_x_producer.py`` asserts against an exploding stub.
    discovery_engine: Any | None = None
    # Unified keyword planner fetch coordinator (P1.7). When wired AND the flag
    # is on, the search strategy claims words from the keyword store and injects
    # them via ``recipe.config["queries"]``; the words are marked ``used`` once
    # the raw candidates are handed off to ``discovery_candidates`` (fetch-only:
    # admission is downstream). ``None`` (default / flag off) → legacy path.
    keyword_fetch: Any | None = None
    _last_run_at: datetime | None = field(default=None, init=False)
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        """Run one X discovery cycle if enabled, due, healthy, and under budget."""
        if not self.enabled:
            return self._skip("disabled")
        if not self._is_due():
            return self._skip("throttled")
        if not self.health_store.is_ready():
            # missing/expired cookie, block, or unexpired rate-limit cooldown.
            return self._skip("unhealthy")

        is_ready_fn = getattr(self.soul_engine, "is_profile_ready", None)
        if callable(is_ready_fn) and not is_ready_fn():
            logger.debug("x producer: soul profile not ready yet")
            return self._skip("no_profile")
        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("x producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        requested_limit = max(1, int(limit or 10))
        items: list[DiscoveredContent] = []

        # 1. Search — soul-driven keyword(s) (the adapter's search strategy
        #    generates keywords from the profile when no explicit query).
        #    Unified keyword planner fetch path (P1.7, flag-gated): claim words
        #    from the store and inject them via recipe.config["queries"]. The
        #    deficit gate is upstream (the controller only invokes the producer
        #    when X is under quota); the distinct floor is ``min_interval`` /
        #    ``_is_due`` above; the daily search budget still gates the run.
        claimed_search: list[Any] = []
        if self._strategy_budget_remaining(SEARCH, requested_limit) > 0:
            coordinator = self.keyword_fetch
            if coordinator is not None and bool(
                getattr(coordinator, "should_claim", lambda: False)()
            ):
                claimed_search = coordinator.claim(_PLATFORM_TWITTER)
                if claimed_search:
                    # P1.8: thread the producing keyword's id onto each candidate
                    # so admit-time yield backfill credits the right word.
                    search_config = {
                        "queries": [item.keyword for item in claimed_search],
                        "keyword_ids": {item.keyword: int(item.id) for item in claimed_search},
                    }
                    items += await self._run_strategy(
                        SEARCH, profile, config=search_config, limit=requested_limit
                    )
                # Flag on but store empty → skip the search fetch this cycle
                # (the planner will refill); feed/creator below still run.
            else:
                items += await self._run_strategy(SEARCH, profile, config={}, limit=requested_limit)

        # 2. For-You — high-visibility; throttled to a low daily cadence AND
        #    auto-paused after repeated feed failures.
        if (
            self.health_store.feed_allowed()
            and self._strategy_budget_remaining(FEED, requested_limit) > 0
        ):
            items += await self._run_strategy(FEED, profile, config={}, limit=requested_limit)

        # 3. Creators — each subscription due for a refresh.
        items += await self._run_creators(profile, requested_limit)

        enqueued = self._enqueue(items)
        # Fetch-only lifecycle: the claimed search words are consumed on the
        # handoff of raw candidates to ``discovery_candidates`` above — mark them
        # ``used`` (admission is downstream; yield backfill is P1.8).
        if claimed_search and self.keyword_fetch is not None:
            self.keyword_fetch.mark_used(claimed_search)
        self._last_run_at = datetime.now(UTC)
        return {"enqueued": enqueued, "discovered": len(items), "reason": "ok"}

    # ── strategy execution ───────────────────────────────────────────

    async def _run_strategy(
        self,
        strategy: str,
        profile: Any,
        *,
        config: dict[str, Any],
        limit: int,
    ) -> list[DiscoveredContent]:
        """Fetch one strategy via the adapter, record health + budget."""
        from openbiliclaw.sources.protocol import SourceRecipe

        recipe = SourceRecipe(
            id=f"x-{strategy}",
            source_type=getattr(self.adapter, "source_type", "twitter"),
            name=f"X-{strategy}",
            strategy=strategy,
            config=dict(config),
        )
        await self._jitter()
        try:
            items = await self.adapter.fetch(recipe, profile, limit)
        except Exception as exc:  # noqa: BLE001 - normalize to a health state
            self.health_store.record_error(exc, strategy=strategy)
            logger.warning("x producer strategy failed: strategy=%s error=%s", strategy, exc)
            return []
        self.health_store.record_success(strategy=strategy)
        self._record_run(strategy)
        self._stamp_score_thresholds(items, strategy=strategy)
        return list(items)

    async def _run_creators(self, profile: Any, limit: int) -> list[DiscoveredContent]:
        """Fetch each subscription due for a refresh, oldest first, under budget."""
        if self._strategy_budget_remaining(CREATOR, limit) <= 0:
            return []
        try:
            due = self.creator_store.due_for_fetch(hours=self.creator_refresh_hours)
        except Exception:
            logger.debug("x producer: creator due-list unavailable", exc_info=True)
            return []
        out: list[DiscoveredContent] = []
        for sub in due:
            if self._strategy_budget_remaining(CREATOR, limit) <= 0:
                break
            handle = str(sub.get("handle", "") or "").strip()
            if not handle:
                continue
            fetched = await self._run_strategy(
                CREATOR, profile, config={"handle": handle}, limit=limit
            )
            out += fetched
            sub_id = int(sub.get("id", 0) or 0)
            if sub_id > 0:
                self.creator_store.mark_fetched(sub_id)
        return out

    # ── candidate enqueue (fetch-only) ───────────────────────────────

    def _enqueue(self, items: list[DiscoveredContent]) -> int:
        """Enqueue raw items into ``discovery_candidates`` (never content_cache)."""
        if not items:
            return 0
        writes = [
            discovered_content_to_candidate_write(item, source_context=item.source_strategy)
            for item in items
        ]
        try:
            return int(self.database.enqueue_discovery_candidates(writes))
        except Exception:
            logger.warning("x producer: candidate enqueue failed", exc_info=True)
            return 0

    @staticmethod
    def _stamp_score_thresholds(items: list[DiscoveredContent], *, strategy: str) -> None:
        threshold = _X_SCORE_THRESHOLDS.get(strategy, 0.60)
        for item in items:
            try:
                if float(getattr(item, "score_threshold", 0.0) or 0.0) > 0:
                    continue
                item.score_threshold = threshold
            except Exception:
                logger.debug("x producer: failed to stamp score threshold", exc_info=True)

    # ── budgets + interval ───────────────────────────────────────────

    def _strategy_budget_remaining(self, strategy: str, per_run_budget: int) -> int:
        """Return runnable units for one strategy today.

        ``daily_*_budget == 0`` means no per-day cap (bounded by the runtime
        deficit ``per_run_budget``). ``< 0`` disables the strategy outright.
        Mirrors the YouTube producer convention.
        """
        budget = {
            SEARCH: int(self.daily_search_budget),
            FEED: int(self.daily_feed_budget),
            CREATOR: int(self.daily_creator_budget),
        }.get(strategy, 0)
        if budget == 0:
            return max(1, int(per_run_budget))
        if budget < 0:
            return 0
        return max(0, budget - self._consumed_today(strategy))

    def _consumed_today(self, strategy: str) -> int:
        self._ensure_ledger_table()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        row = self.database.conn.execute(
            "SELECT COUNT(*) FROM x_discovery_runs WHERE strategy = ? AND created_at >= ?",
            (strategy, today),
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def _record_run(self, strategy: str) -> None:
        self._ensure_ledger_table()
        self.database.conn.execute(
            "INSERT INTO x_discovery_runs(strategy) VALUES (?)",
            (strategy,),
        )
        self.database.conn.commit()

    def _ensure_ledger_table(self) -> None:
        self.database.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS x_discovery_runs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy   TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_x_discovery_runs_strategy_created
                ON x_discovery_runs(strategy, created_at);
            """
        )
        self.database.conn.commit()

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        if self._last_run_at is None:
            return True
        return datetime.now(UTC) - self._last_run_at >= timedelta(minutes=self.min_interval_minutes)

    async def _jitter(self) -> None:
        """Sleep ``request_interval_seconds`` (+ jitter) between X requests."""
        base = max(0, int(self.request_interval_seconds))
        if base <= 0:
            return
        await asyncio.sleep(base + random.uniform(0, base))

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("x producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"enqueued": 0, "discovered": 0, "reason": reason}


def build_x_discovery_producer(
    *,
    config: Any,
    database: Any,
    soul_engine: Any,
    llm_service: Any,
    keyword_fetch: Any | None = None,
) -> XDiscoveryProducer | None:
    """Build the runtime X producer if the X source is enabled.

    Returns ``None`` (and imports nothing from ``twitter_cli``) when X is
    disabled or the scheduler is off — preserving the lazy-import contract for
    non-X installs. On the enabled path it constructs a single :class:`XClient`
    + the three strategies behind an :class:`XAdapter` (server-side cookie
    replay), and an :class:`XSourceHealthStore` for per-code backoff.
    """
    x_cfg = getattr(getattr(config, "sources", None), "twitter", None)
    if x_cfg is None or not bool(getattr(x_cfg, "enabled", False)):
        return None
    sched_cfg = getattr(config, "scheduler", None)
    if not bool(getattr(sched_cfg, "enabled", True)):
        return None
    if not hasattr(database, "conn"):
        logger.info("x producer disabled: database does not expose task tables")
        return None

    # Lazy imports — only reached on the enabled path.
    from openbiliclaw.discovery.strategies.x import (
        XCreatorStrategy,
        XForYouStrategy,
        XSearchStrategy,
    )
    from openbiliclaw.sources.twitter_adapter import XAdapter
    from openbiliclaw.sources.x_auth import resolve_x_cookie
    from openbiliclaw.sources.x_client import XClient
    from openbiliclaw.sources.x_tasks import XCreatorStore
    from openbiliclaw.storage.x_health import XSourceHealthStore

    cookie = resolve_x_cookie(
        data_dir=config.data_path,
        cookie_env=str(getattr(x_cfg, "cookie_env", "OPENBILICLAW_X_COOKIE")),
    )
    x_client = XClient(cookie=cookie)
    adapter = XAdapter(
        client=x_client,
        search=XSearchStrategy(client=x_client, llm_service=llm_service),
        feed=XForYouStrategy(client=x_client),
        creator=XCreatorStrategy(client=x_client),
    )
    return XDiscoveryProducer(
        database=database,
        soul_engine=soul_engine,
        adapter=adapter,
        creator_store=XCreatorStore(database),
        health_store=XSourceHealthStore(database),
        enabled=True,
        min_interval_minutes=int(getattr(x_cfg, "min_interval_minutes", 60)),
        daily_search_budget=int(getattr(x_cfg, "daily_search_budget", 0)),
        daily_feed_budget=int(getattr(x_cfg, "daily_feed_budget", 0)),
        daily_creator_budget=int(getattr(x_cfg, "daily_creator_budget", 0)),
        request_interval_seconds=int(getattr(x_cfg, "request_interval_seconds", 3)),
        keyword_fetch=keyword_fetch,
    )
