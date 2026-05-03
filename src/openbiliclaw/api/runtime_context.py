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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from fastapi import FastAPI

    from openbiliclaw.config import Config

logger = logging.getLogger(__name__)


@dataclass
class RuntimeContext:
    """Mutable holder for all runtime components used by API endpoints."""

    # ── Stable (never rebuilt) ──────────────────────────────────────
    database: Any = None
    memory_manager: Any = None
    event_hub: Any = None

    # ── Swappable (rebuilt on hot-reload) ───────────────────────────
    config: Any = None
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

    def rebuild_from_config(self, new_config: Config) -> None:
        """Rebuild all swappable components from *new_config*.

        Construction is performed entirely into local variables first.
        Only after **every** component succeeds are the attributes
        assigned — guaranteeing atomic rollback on failure.

        This method is synchronous; because the asyncio event loop is
        single-threaded no endpoint handler can interleave during the
        attribute-assignment sweep.
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
        from openbiliclaw.llm.service import LLMService
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
        new_llm_service = LLMService(
            registry=new_registry,
            memory=self.memory_manager,
            usage_recorder=new_usage_recorder,
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
        new_soul_engine = SoulEngine(
            llm=new_registry,  # type: ignore[arg-type]
            memory=self.memory_manager,
            usage_recorder=new_usage_recorder,
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
        )
        trending_strategy = TrendingStrategy(
            bilibili_client=new_bilibili_client,
            llm_service=new_llm_service,
            concurrency=concurrency,
        )
        related_strategy = RelatedChainStrategy(
            bilibili_client=new_bilibili_client,
            llm_service=new_llm_service,
            memory_manager=cast("Any", self.memory_manager),
            search_strategy=search_strategy,
            trending_strategy=trending_strategy,
            concurrency=concurrency,
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
        if hasattr(self.database, "conn"):
            from openbiliclaw.runtime.xhs_producer import XhsTaskProducer
            from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

            xhs_cfg = getattr(new_config.sources, "xiaohongshu", None)
            sched_cfg = getattr(new_config, "scheduler", None)
            new_xhs_producer = XhsTaskProducer(
                task_queue=XhsTaskQueue(self.database),
                soul_engine=new_soul_engine,
                llm_service=new_llm_service,
                enabled=bool(getattr(sched_cfg, "enabled", True)),
                daily_budget=int(getattr(xhs_cfg, "daily_search_budget", 30)),
            )

        new_runtime_controller = ContinuousRefreshController(
            memory_manager=self.memory_manager,
            database=self.database,
            soul_engine=new_soul_engine,
            discovery_engine=new_discovery_engine,
            recommendation_engine=new_recommendation_engine,
            pool_target_count=new_config.scheduler.pool_target_count,
            event_hub=self.event_hub,
            xhs_producer=new_xhs_producer,
        )

        # 9. Account sync
        new_account_sync = AccountSyncService(
            memory_manager=self.memory_manager,
            bilibili_client=new_bilibili_client,
            soul_engine=new_soul_engine,
            sync_interval_hours=new_config.scheduler.account_sync_interval_hours,
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

        # Start new tasks from the freshly-built components
        run_forever = getattr(self.runtime_controller, "run_forever", None)
        app.state.refresh_task = (
            asyncio.create_task(run_forever()) if callable(run_forever) else None
        )

        sync_forever = getattr(self.account_sync_service, "run_forever", None)
        app.state.account_sync_task = (
            asyncio.create_task(sync_forever()) if callable(sync_forever) else None
        )

        update_forever = getattr(self.auto_update_service, "run_forever", None)
        app.state.auto_update_task = (
            asyncio.create_task(update_forever()) if callable(update_forever) else None
        )

        # Kick speculator to seed speculative interests
        if self.soul_engine is not None:
            try:
                profile = await self.soul_engine.get_profile()
                speculator = getattr(self.soul_engine, "_speculator", None)
                if speculator is not None:
                    await speculator.force_tick(profile)
            except Exception:
                pass  # Profile not initialized yet — skip silently

        logger.info("Background tasks restarted after hot-reload")


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

    # Build all swappable components via the same path used for hot-reload
    ctx.rebuild_from_config(config)
    return ctx
