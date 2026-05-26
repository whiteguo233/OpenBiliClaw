"""Mutable runtime component container with config hot-reload support.

All FastAPI endpoint closures access runtime components through a single
``RuntimeContext`` instance.  When configuration changes at runtime (via
``PUT /api/config``), the context atomically rebuilds every swappable
component so the new settings take effect immediately — no server restart
required.

**Stable components** (never rebuilt):
  - ``database`` — owns the SQLite connection
  - ``memory_manager`` — owns file-backed memory layers
  - ``event_hub`` — holds live WebSocket subscriber queues
  - ``presence`` — tracks shared extension runtime-stream presence

**Swappable components** (rebuilt on hot-reload):
  - ``llm_registry``, ``llm_service``, ``bilibili_client``
  - ``soul_engine``, ``dialogue``
  - ``discovery_engine``, ``recommendation_engine``
  - ``runtime_controller``, ``account_sync_service``
  - ``auto_update_service``
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from openbiliclaw.config import llm_concurrency_from_config as _llm_concurrency_from_config
from openbiliclaw.runtime.presence import PresenceTracker
from openbiliclaw.runtime.presence import background_llm_work_allowed as _gate
from openbiliclaw.runtime.source_policy import effective_pool_source_shares
from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry

if TYPE_CHECKING:
    from fastapi import FastAPI

    from openbiliclaw.config import Config
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)


def _pool_source_shares_from_config(config: Any) -> dict[str, int]:
    return effective_pool_source_shares(config)


def build_youtube_discovery_strategies(
    *,
    config: Any,
    client: Any,
    llm_service: Any,
    memory: Any,
    concurrency: Any,
    database: Database | None = None,
    strategy_unit_budget: dict[str, int] | None = None,
) -> list[Any]:
    """Build YouTube discovery strategies from `[sources.youtube]` config."""

    from openbiliclaw.discovery.strategies.youtube import (
        YoutubeChannelStrategy,
        YoutubeSearchStrategy,
        YoutubeTrendingStrategy,
    )

    yt_cfg = getattr(getattr(config, "sources", None), "youtube", None)
    budgets = strategy_unit_budget or {}
    search_budget = int(budgets.get("yt_search", getattr(yt_cfg, "daily_search_budget", 6)))
    trending_budget = int(budgets.get("yt_trending", getattr(yt_cfg, "daily_trending_budget", 50)))
    channel_budget = int(budgets.get("yt_channel", getattr(yt_cfg, "daily_channel_budget", 10)))
    return [
        YoutubeSearchStrategy(
            client=client,
            llm_service=llm_service,
            concurrency=concurrency,
            database=database,
            queries_per_run=max(0, search_budget),
        ),
        YoutubeTrendingStrategy(
            client=client,
            llm_service=llm_service,
            concurrency=concurrency,
            database=database,
            fetch_limit=max(0, trending_budget),
        ),
        YoutubeChannelStrategy(
            client=client,
            llm_service=llm_service,
            memory=memory,
            concurrency=concurrency,
            database=database,
            max_channels=max(0, channel_budget),
        ),
    ]


def _youtube_strategy_units_used(strategy: Any, *, fallback: int) -> int:
    """Return the execution units consumed by one YouTube strategy run."""
    name = str(getattr(strategy, "name", ""))
    intermediates = getattr(strategy, "last_intermediates", {}) or {}
    if name == "yt_search":
        queries = intermediates.get("queries")
        if isinstance(queries, list):
            return len(queries)
    if name == "yt_trending":
        fetched = intermediates.get("fetched")
        if isinstance(fetched, int):
            return fetched
    if name == "yt_channel":
        channel_ids = intermediates.get("channel_ids")
        if isinstance(channel_ids, list):
            return len(channel_ids)
    return max(0, int(fallback))


def _build_yt_scraper_client() -> Any:
    from openbiliclaw.youtube.client import YtScraperClient

    return YtScraperClient()


def build_youtube_discovery_producer(
    *,
    config: Any,
    database: Any,
    soul_engine: Any,
    discovery_engine: Any,
    llm_service: Any,
    memory: Any,
    concurrency: Any,
) -> Any | None:
    """Build the runtime YouTube producer if YouTube discovery is enabled."""
    yt_cfg = getattr(getattr(config, "sources", None), "youtube", None)
    if yt_cfg is None or not bool(getattr(yt_cfg, "enabled", False)):
        return None
    scheduler = getattr(config, "scheduler", None)
    if not bool(getattr(scheduler, "enabled", True)):
        return None
    if not hasattr(database, "conn"):
        logger.info("youtube producer disabled: database does not expose sqlite connection")
        return None

    from openbiliclaw.runtime.youtube_producer import (
        YoutubeDiscoveryProducer,
        YoutubeStrategyRunResult,
    )

    try:
        yt_client = _build_yt_scraper_client()
    except ImportError as exc:
        logger.info("youtube producer disabled: YouTube dependencies unavailable: %s", exc)
        return None

    async def _discover(
        profile: Any,
        *,
        strategy: str,
        unit_budget: int,
        result_limit: int,
    ) -> YoutubeStrategyRunResult:
        strategies = build_youtube_discovery_strategies(
            config=config,
            client=yt_client,
            llm_service=llm_service,
            memory=memory,
            concurrency=concurrency,
            database=database,
            strategy_unit_budget={strategy: unit_budget},
        )
        selected = [item for item in strategies if item.name == strategy]
        if not selected:
            return YoutubeStrategyRunResult(items=[], units_used=0, source_counts={})

        selected_strategy = selected[0]
        discovery_engine.register_strategy(selected_strategy)
        raw_items = await discovery_engine.discover(
            profile,
            strategies=[strategy],
            limit=max(1, int(result_limit)),
        )
        items = [
            item
            for item in raw_items
            if str(getattr(item, "source_platform", "")) == "youtube"
            or str(getattr(item, "source_strategy", "")).startswith("yt_")
        ]
        units_used = _youtube_strategy_units_used(
            selected_strategy,
            fallback=max(0, int(unit_budget)),
        )
        return YoutubeStrategyRunResult(
            items=items,
            units_used=units_used,
            source_counts={strategy: len(items)},
        )

    return YoutubeDiscoveryProducer(
        database=database,
        soul_engine=soul_engine,
        discover=_discover,
        enabled=True,
        min_interval_minutes=int(getattr(yt_cfg, "min_interval_minutes", 60)),
        daily_search_budget=int(getattr(yt_cfg, "daily_search_budget", 6)),
        daily_trending_budget=int(getattr(yt_cfg, "daily_trending_budget", 50)),
        daily_channel_budget=int(getattr(yt_cfg, "daily_channel_budget", 10)),
    )


@dataclass
class RuntimeContext:
    """Mutable holder for all runtime components used by API endpoints."""

    # ── Stable (never rebuilt) ──────────────────────────────────────
    database: Any = None
    memory_manager: Any = None
    event_hub: Any = None
    presence: PresenceTracker = field(default_factory=PresenceTracker)
    # v0.3.63+: tracks every detached ``asyncio.create_task`` spawned by
    # the runtime (refresh manual / per-strategy precompute, recommendation
    # engine classify+delight, prewarm helpers, per-event triggers). On
    # ``rebuild_from_config`` these are cancelled before new runtime objects
    # are constructed so old detached work doesn't compete with the freshly
    # built runtime for SQLite writes / LLM tokens.
    task_registry: BackgroundTaskRegistry = field(default_factory=BackgroundTaskRegistry)

    # ── Swappable (rebuilt on hot-reload) ───────────────────────────
    config: Any = None
    degraded: bool = False
    degraded_reason: str = ""
    degraded_issues: list[Any] = field(default_factory=list)
    llm_registry: Any = None
    llm_service: Any = None
    bilibili_client: Any = None
    soul_engine: Any = None
    dialogue: Any = None
    discovery_engine: Any = None
    recommendation_engine: Any = None
    runtime_controller: Any = None
    account_sync_service: Any = None
    auto_update_service: Any = None

    def background_llm_work_allowed(self) -> bool:
        """Return whether daemon-owned background LLM / embedding work may run."""
        scheduler = getattr(getattr(self, "config", None), "scheduler", None)
        return _gate(scheduler, self.presence)

    async def rebuild_from_config(self, new_config: Config) -> None:
        """Rebuild all swappable components from *new_config*.

        v0.3.63+: this is now ``async`` so the call can ``await`` the
        background-task registry's ``cancel_all`` BEFORE constructing
        new runtime objects. Without that step, detached tasks created
        by the OLD recommendation engine / refresh controller (per-event
        triggers, per-strategy precompute, prewarm helpers) keep running
        after rebuild and compete with the new runtime for SQLite writes
        and LLM tokens for several seconds.

        Construction itself is still synchronous and performed entirely
        into local variables first — only after **every** component
        succeeds are the attributes assigned, so atomic rollback on
        failure is preserved. The asyncio event loop is single-threaded
        so no endpoint handler can interleave during the attribute-
        assignment sweep.
        """
        cancelled = await self.task_registry.cancel_all()
        if cancelled:
            logger.info(
                "Hot-reload: cancelled %d background task(s) before rebuild",
                cancelled,
            )
        self._rebuild_components(new_config)

    def _rebuild_components(self, new_config: Config) -> None:
        """Synchronous component construction shared by hot-reload and startup.

        ``rebuild_from_config`` (async) calls this after cancelling
        in-flight background tasks. ``build_runtime_context`` calls this
        directly during initial construction — at that point the
        registry is empty so no cancel step is required, and remaining
        sync simplifies the FastAPI startup path which is itself sync.
        """
        from openbiliclaw.bilibili.api import BilibiliAPIClient
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.discovery.engine import (
            ContentDiscoveryEngine,
            DiscoveryConcurrencyController,
        )
        from openbiliclaw.discovery.strategies.strategies import (
            ExploreStrategy,
            RelatedChainStrategy,
            SearchStrategy,
            TrendingStrategy,
        )
        from openbiliclaw.llm import build_llm_registry
        from openbiliclaw.llm.registry import build_embedding_service
        from openbiliclaw.llm.service import LLMService, module_overrides_from_config
        from openbiliclaw.llm.usage_recorder import UsageRecorder
        from openbiliclaw.recommendation.engine import RecommendationEngine
        from openbiliclaw.runtime.account_sync import AccountSyncService
        from openbiliclaw.runtime.refresh import ContinuousRefreshController
        from openbiliclaw.runtime.updater import AutoUpdateService
        from openbiliclaw.soul.dialogue import SocraticDialogue
        from openbiliclaw.soul.engine import SoulEngine

        # 1. LLM layer (with usage ledger so ``openbiliclaw cost`` has data)
        new_registry = build_llm_registry(new_config)
        new_usage_recorder = UsageRecorder(sink=self.database)
        new_module_overrides = module_overrides_from_config(new_config)
        llm_concurrency = _llm_concurrency_from_config(new_config)
        new_llm_service = LLMService(
            registry=new_registry,
            memory=self.memory_manager,
            usage_recorder=new_usage_recorder,
            module_overrides=new_module_overrides,
            concurrency=llm_concurrency,
        )

        # 2. Bilibili client
        new_bilibili_client = BilibiliAPIClient(
            cookie=resolve_runtime_cookie(
                data_dir=new_config.data_path,
                configured_cookie=new_config.bilibili.cookie,
            )
        )

        # 3. Soul engine (reuses stable memory_manager)
        # usage_recorder is forwarded so the internal LLMService SoulEngine
        # builds (used by preference / awareness / insight / profile_builder
        # / speculator) writes to the cost ledger with caller tags. Before
        # this was wired, ``soul.*`` callers were entirely missing from
        # ``openbiliclaw cost --by caller`` and speculator failures
        # surfaced as silent "0 new" instead of explicit WARNs.
        # Defensive getattr chain: legacy test fixtures and partial
        # config stubs may not expose the new `soul.preference` block.
        # Default to True when the field is absent: quick-exit rows should
        # not self-feed into preferences, while explicit dislikes still
        # remain available as negative evidence.
        soul_cfg = getattr(new_config, "soul", None)
        preference_cfg = getattr(soul_cfg, "preference", None) if soul_cfg else None
        satisfaction_filter_enabled = bool(
            getattr(preference_cfg, "satisfaction_filter_enabled", True)
        )
        new_soul_engine = SoulEngine(
            llm=new_registry,
            memory=self.memory_manager,
            usage_recorder=new_usage_recorder,
            satisfaction_filter_enabled=satisfaction_filter_enabled,
            module_overrides=new_module_overrides,
            llm_concurrency=llm_concurrency,
            speculation_interval_minutes=int(
                getattr(new_config.scheduler, "speculation_interval_minutes", 10)
            ),
            speculation_ttl_days=int(getattr(new_config.scheduler, "speculation_ttl_days", 3)),
            speculation_cooldown_days=int(
                getattr(new_config.scheduler, "speculation_cooldown_days", 7)
            ),
            speculation_confirmation_threshold=int(
                getattr(new_config.scheduler, "speculation_confirmation_threshold", 3)
            ),
            speculation_max_active=int(getattr(new_config.scheduler, "speculation_max_active", 5)),
            speculation_max_primary_interests=int(
                getattr(new_config.scheduler, "speculation_max_primary_interests", 15)
            ),
            speculation_max_secondary_interests=int(
                getattr(new_config.scheduler, "speculation_max_secondary_interests", 60)
            ),
            avoidance_speculation_interval_minutes=int(
                getattr(new_config.scheduler, "avoidance_speculation_interval_minutes", 10)
            ),
            avoidance_speculation_ttl_days=int(
                getattr(new_config.scheduler, "avoidance_speculation_ttl_days", 3)
            ),
            avoidance_speculation_cooldown_days=int(
                getattr(new_config.scheduler, "avoidance_speculation_cooldown_days", 7)
            ),
            avoidance_speculation_confirmation_threshold=int(
                getattr(new_config.scheduler, "avoidance_speculation_confirmation_threshold", 3)
            ),
            avoidance_speculation_max_active=int(
                getattr(new_config.scheduler, "avoidance_speculation_max_active", 5)
            ),
            speculator_idle_interval_minutes=int(
                getattr(new_config.scheduler, "speculator_idle_interval_minutes", 30)
            ),
            feedback_batch_threshold=int(
                getattr(new_config.scheduler, "feedback_batch_threshold", 3)
            ),
        )

        # 4. Embedding service
        new_embedding_service = build_embedding_service(new_config, new_registry)

        # 5. Share embedding with soul pipeline for semantic purges
        set_emb = getattr(new_soul_engine, "set_embedding_service", None)
        if callable(set_emb):
            set_emb(new_embedding_service)

        # 6. Recommendation engine
        from openbiliclaw.recommendation.curator import PoolCurator

        new_curator = PoolCurator(self.database)
        new_recommendation_engine = RecommendationEngine(
            llm=new_llm_service,
            database=self.database,
            curator=new_curator,
            embedding_service=new_embedding_service,
            task_registry=self.task_registry,
        )

        # 7. Discovery engine + strategies
        concurrency = DiscoveryConcurrencyController(
            bilibili_request_concurrency=2,
            llm_evaluation_concurrency=2,
        )
        new_discovery_engine = ContentDiscoveryEngine(
            llm_service=new_llm_service,
            database=self.database,
            concurrency=concurrency,
            embedding_service=new_embedding_service,
        )
        search_strategy = SearchStrategy(
            llm_service=new_llm_service,
            bilibili_client=new_bilibili_client,
            concurrency=concurrency,
            database=self.database,
        )
        trending_strategy = TrendingStrategy(
            bilibili_client=new_bilibili_client,
            llm_service=new_llm_service,
            concurrency=concurrency,
            database=self.database,
        )
        related_strategy = RelatedChainStrategy(
            bilibili_client=new_bilibili_client,
            llm_service=new_llm_service,
            memory_manager=cast("Any", self.memory_manager),
            search_strategy=search_strategy,
            trending_strategy=trending_strategy,
            concurrency=concurrency,
            database=self.database,
        )
        explore_strategy = ExploreStrategy(
            llm_service=new_llm_service,
            bilibili_client=new_bilibili_client,
            concurrency=concurrency,
            embedding_service=new_embedding_service,
            database=cast("Any", self.database),
        )
        new_discovery_engine.register_strategy(search_strategy)
        new_discovery_engine.register_strategy(trending_strategy)
        new_discovery_engine.register_strategy(related_strategy)
        new_discovery_engine.register_strategy(explore_strategy)

        # 7b. Register Bilibili source adapter (multi-source Phase 1)
        from openbiliclaw.sources.bilibili_adapter import BilibiliAdapter

        bilibili_adapter = BilibiliAdapter(
            search=search_strategy,
            trending=trending_strategy,
            related_chain=related_strategy,
            explore=explore_strategy,
        )
        new_discovery_engine.register_adapter(bilibili_adapter)

        # Register Xiaohongshu adapter — content enters the pool via the
        # extension's API endpoints (POST /api/sources/xhs/observed-urls),
        # not via adapter.fetch(). The adapter is a stub so the registry
        # knows "xiaohongshu" is a valid source type.
        from openbiliclaw.sources.xiaohongshu_adapter import XiaohongshuAdapter

        xiaohongshu_adapter = XiaohongshuAdapter()
        new_discovery_engine.register_adapter(xiaohongshu_adapter)

        # 8. Continuous refresh controller
        new_xhs_producer: Any = None
        new_douyin_producer: Any = None
        new_youtube_producer: Any = None
        if hasattr(self.database, "conn"):
            from openbiliclaw.runtime.xhs_producer import XhsTaskProducer
            from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

            xhs_cfg = getattr(new_config.sources, "xiaohongshu", None)
            sched_cfg = getattr(new_config, "scheduler", None)
            xhs_enabled = bool(getattr(xhs_cfg, "enabled", False)) and bool(
                getattr(sched_cfg, "enabled", True)
            )
            new_xhs_producer = XhsTaskProducer(
                task_queue=XhsTaskQueue(self.database),
                soul_engine=new_soul_engine,
                llm_service=new_llm_service,
                enabled=xhs_enabled,
                daily_budget=int(getattr(xhs_cfg, "daily_search_budget", 30)),
            )
            from openbiliclaw.runtime.douyin_producer import build_douyin_discovery_producer

            new_douyin_producer = build_douyin_discovery_producer(
                config=new_config,
                database=self.database,
                soul_engine=new_soul_engine,
                discovery_engine=new_discovery_engine,
            )
            new_youtube_producer = build_youtube_discovery_producer(
                config=new_config,
                database=self.database,
                soul_engine=new_soul_engine,
                discovery_engine=new_discovery_engine,
                llm_service=new_llm_service,
                memory=cast("Any", self.memory_manager),
                concurrency=concurrency,
            )

        new_runtime_controller = ContinuousRefreshController(
            memory_manager=self.memory_manager,
            database=self.database,
            soul_engine=new_soul_engine,
            discovery_engine=new_discovery_engine,
            recommendation_engine=new_recommendation_engine,
            pool_target_count=new_config.scheduler.pool_target_count,
            pool_source_shares=_pool_source_shares_from_config(new_config),
            signal_event_threshold=int(getattr(new_config.scheduler, "signal_event_threshold", 6)),
            trending_refresh_hours=int(getattr(new_config.scheduler, "trending_refresh_hours", 3)),
            explore_refresh_hours=int(getattr(new_config.scheduler, "explore_refresh_hours", 12)),
            check_interval_seconds=int(
                getattr(new_config.scheduler, "refresh_check_interval_seconds", 60)
            ),
            proactive_push_interval_seconds=int(
                getattr(new_config.scheduler, "proactive_push_interval_seconds", 120)
            ),
            discovery_limit=int(getattr(new_config.scheduler, "discovery_limit", 30)),
            event_hub=self.event_hub,
            xhs_producer=new_xhs_producer,
            douyin_producer=new_douyin_producer,
            youtube_producer=new_youtube_producer,
            scheduler_config=new_config.scheduler,
            presence=self.presence,
            task_registry=self.task_registry,
        )

        # 9. Account sync
        new_account_sync = AccountSyncService(
            memory_manager=self.memory_manager,
            bilibili_client=new_bilibili_client,
            soul_engine=new_soul_engine,
            sync_interval_hours=new_config.scheduler.account_sync_interval_hours,
            llm_work_allowed=self.background_llm_work_allowed,
        )

        # 10. Dialogue (with source management tools)
        from openbiliclaw.sources.tools import SOURCE_TOOLS, SourceToolDispatcher

        source_tool_dispatcher = SourceToolDispatcher(self.database)
        new_dialogue = SocraticDialogue(
            llm=None,
            soul_engine=new_soul_engine,
            llm_service=new_llm_service,
            session="popup",
            tools=SOURCE_TOOLS,
            tool_dispatcher=source_tool_dispatcher,
        )

        # 11. Auto-update service
        try:
            new_auto_update = AutoUpdateService(
                enabled=new_config.scheduler.auto_update_enabled,
                check_interval_hours=new_config.scheduler.auto_update_check_interval_hours,
            )
        except Exception:
            new_auto_update = AutoUpdateService(enabled=True)

        # ── Atomic swap ─────────────────────────────────────────────
        # All construction succeeded → assign attributes.
        self.config = new_config
        self.llm_registry = new_registry
        self.llm_service = new_llm_service
        self.bilibili_client = new_bilibili_client
        self.soul_engine = new_soul_engine
        self.dialogue = new_dialogue
        self.discovery_engine = new_discovery_engine
        self.recommendation_engine = new_recommendation_engine
        self.runtime_controller = new_runtime_controller
        self.account_sync_service = new_account_sync
        self.auto_update_service = new_auto_update

        logger.info(
            "Hot-reload complete — rebuilt %d swappable components",
            11,
        )

    async def restart_background_tasks(self, app: FastAPI) -> None:
        """Cancel old background tasks and start new ones from current components."""
        # Cancel existing tasks
        for attr in ("refresh_task", "account_sync_task", "auto_update_task"):
            task = getattr(app.state, attr, None)
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        # Start new tasks from the freshly-built components.
        # v0.3.63+: route through ``self.task_registry.track`` so the
        # next hot-reload's ``cancel_all`` cleanly stops them too.
        run_forever = getattr(self.runtime_controller, "run_forever", None)
        app.state.refresh_task = (
            self.task_registry.track("refresh_loop", run_forever())
            if callable(run_forever)
            else None
        )

        sync_forever = getattr(self.account_sync_service, "run_forever", None)
        app.state.account_sync_task = (
            self.task_registry.track("account_sync_loop", sync_forever())
            if callable(sync_forever)
            else None
        )

        update_forever = getattr(self.auto_update_service, "run_forever", None)
        app.state.auto_update_task = (
            self.task_registry.track("auto_update_loop", update_forever())
            if callable(update_forever)
            else None
        )

        llm_work_allowed = self.background_llm_work_allowed()

        # Kick speculators to seed speculative interests / avoidances
        if self.soul_engine is not None and llm_work_allowed:
            try:
                profile = await self.soul_engine.get_profile()
                runtime_state: dict[str, object] = {}
                load_runtime_state = getattr(
                    self.memory_manager,
                    "load_discovery_runtime_state",
                    None,
                )
                if callable(load_runtime_state):
                    loaded = load_runtime_state()
                    if isinstance(loaded, dict):
                        runtime_state = loaded

                speculator = getattr(self.soul_engine, "_speculator", None)
                if speculator is not None:
                    feedback_history: object = runtime_state.get("probe_feedback_history", [])
                    self.task_registry.track(
                        "post_reload_speculate",
                        self._safe_post_reload_speculate(
                            speculator,
                            profile,
                            feedback_history,
                        ),
                    )
                    logger.debug("post-reload speculator scheduled as background task")

                avoidance_speculator = getattr(self.soul_engine, "_avoidance_speculator", None)
                if avoidance_speculator is not None:
                    avoidance_feedback: object = runtime_state.get(
                        "avoidance_probe_feedback_history", []
                    )
                    self.task_registry.track(
                        "post_reload_avoidance_speculate",
                        self._safe_post_reload_speculate(
                            avoidance_speculator,
                            profile,
                            avoidance_feedback,
                        ),
                    )
                    logger.debug("post-reload avoidance speculator scheduled as background task")
            except Exception:
                pass  # Profile not initialized yet — skip silently

        # v0.3.45+: warm the recommendation MMR embedding L2 cache for
        # the existing pool. The per-item warm hooks only catch items
        # added *after* this code lands; without a startup pass, the
        # first popup "换一批" pays a cold-fetch ~10-60s on day-1 of a
        # deploy. Detached so we don't block API readiness.
        prewarm_pool = getattr(self.recommendation_engine, "prewarm_pool_mmr_embeddings", None)
        if callable(prewarm_pool) and llm_work_allowed:
            self.task_registry.track(
                "prewarm_pool_mmr_embeddings",
                self._safe_prewarm_pool_mmr_embeddings(prewarm_pool),
            )

        logger.info("Background tasks restarted after hot-reload")

    @staticmethod
    async def _safe_post_reload_speculate(
        speculator: Any,
        profile: Any,
        feedback_history: object,
    ) -> None:
        """Run post-reload speculation without blocking config PUT."""
        try:
            try:
                await speculator.force_tick(
                    profile,
                    feedback_history=feedback_history,
                )
            except TypeError:
                await speculator.force_tick(profile)
        except Exception:
            pass

    @staticmethod
    async def _safe_prewarm_pool_mmr_embeddings(prewarm_callable: Any) -> None:
        """Run startup MMR prewarm with retry-on-low-coverage.

        v0.3.54+: production logs (2026-05-05) showed
        ``MMR embedding fetch: coverage=0/40`` for 31 minutes after
        daemon start — Ollama was 502'ing during the prewarm window
        and the single-shot startup task gave up. Loop with
        exponential backoff so a slow Ollama warmup doesn't lock the
        cache cold for half an hour. Stops after 5 attempts (≈31s)
        OR when prewarm returns >0 (i.e. some embeddings landed).
        Failures swallowed silently so pool MMR cache lazy-fills via
        normal traffic if all 5 attempts truly fail.
        """
        delay = 2.0
        for attempt in range(1, 6):
            try:
                warmed = await prewarm_callable()
                if isinstance(warmed, int) and warmed > 0:
                    return
                logger.info(
                    "Startup prewarm_pool_mmr_embeddings attempt %d warmed=0 — retry in %.1fs",
                    attempt,
                    delay,
                )
            except Exception:
                logger.warning(
                    "Startup prewarm_pool_mmr_embeddings attempt %d failed; retry in %.1fs",
                    attempt,
                    delay,
                    exc_info=True,
                )
            if attempt >= 5:
                break
            await asyncio.sleep(delay)
            delay *= 2
        logger.info(
            "Startup prewarm_pool_mmr_embeddings gave up after retries — "
            "cache will lazy-fill from regular serve()/discovery traffic"
        )


def build_runtime_context(
    config: Config,
    *,
    memory_manager: Any | None = None,
    database: Any | None = None,
    event_hub: Any | None = None,
) -> RuntimeContext:
    """Construct a fully-wired ``RuntimeContext`` from a ``Config``.

    Stable components (``database``, ``memory_manager``, ``event_hub``)
    are created here if not supplied.  All swappable components are built
    by delegating to ``RuntimeContext.rebuild_from_config``.
    """
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.runtime.events import RuntimeEventHub
    from openbiliclaw.storage.database import Database

    # ── Stable components ───────────────────────────────────────────
    created_runtime_database = False
    if database is None:
        database = Database(config.data_path / "openbiliclaw.db")
        database.initialize()
        created_runtime_database = True
    if memory_manager is None:
        # Only share the database handle with memory_manager when WE created
        # it — matches the original create_app() contract that callers who
        # inject their own database don't expect it to be shared.
        shared_database = database if created_runtime_database else None
        memory_manager = MemoryManager(config.data_path, database=shared_database)
        memory_manager.initialize()
    if event_hub is None:
        event_hub = RuntimeEventHub()

    # Wire the soul-layer change callback so any code path that updates
    # the profile (init, cognition cycle, dialogue ingestion, manual
    # rebuild …) automatically broadcasts a ``profile_updated`` event
    # over the WebSocket. The popup listens and re-fetches without
    # requiring a manual ``init_completed`` poke.
    setter = getattr(memory_manager, "set_profile_change_callback", None)
    if callable(setter):

        async def _on_profile_changed() -> None:
            publish = getattr(event_hub, "publish", None)
            if callable(publish):
                with suppress(Exception):
                    await publish(
                        {
                            "type": "profile_updated",
                            "phase": "ready",
                            "message": "画像已更新",
                        }
                    )

        setter(_on_profile_changed)

    ctx = RuntimeContext(
        database=database,
        memory_manager=memory_manager,
        event_hub=event_hub,
    )

    # Build all swappable components via the same path used for hot-reload.
    # ``_rebuild_components`` is the sync portion shared with
    # ``rebuild_from_config``; the async wrapper's ``cancel_all`` is a
    # no-op here because the registry was just created and is empty.
    ctx._rebuild_components(config)
    return ctx


def build_degraded_runtime_context(
    config: Config,
    *,
    memory_manager: Any | None = None,
    database: Any | None = None,
    event_hub: Any | None = None,
    exc: Exception | None = None,
) -> RuntimeContext:
    """Construct a minimal context that can serve config recovery endpoints.

    ``build_runtime_context`` intentionally stays strict. This degraded
    constructor is used only by FastAPI startup after registry construction
    fails, so the popup can still read and repair config.toml.
    """
    from openbiliclaw.config import ConfigIssue
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.runtime.events import RuntimeEventHub
    from openbiliclaw.storage.database import Database

    created_runtime_database = False
    if database is None:
        database = Database(config.data_path / "openbiliclaw.db")
        database.initialize()
        created_runtime_database = True
    if memory_manager is None:
        shared_database = database if created_runtime_database else None
        memory_manager = MemoryManager(config.data_path, database=shared_database)
        memory_manager.initialize()
    if event_hub is None:
        event_hub = RuntimeEventHub()

    setter = getattr(memory_manager, "set_profile_change_callback", None)
    if callable(setter):

        async def _on_profile_changed() -> None:
            publish = getattr(event_hub, "publish", None)
            if callable(publish):
                with suppress(Exception):
                    await publish(
                        {
                            "type": "profile_updated",
                            "phase": "ready",
                            "message": "画像已更新",
                        }
                    )

        setter(_on_profile_changed)

    message = str(exc) if exc is not None else "LLM registry unavailable"
    return RuntimeContext(
        database=database,
        memory_manager=memory_manager,
        event_hub=event_hub,
        config=config,
        degraded=True,
        degraded_reason="llm_registry_unavailable",
        degraded_issues=[
            ConfigIssue(
                field="llm",
                message=f"LLM registry unavailable: {message}",
                severity="blocking",
            )
        ],
    )
