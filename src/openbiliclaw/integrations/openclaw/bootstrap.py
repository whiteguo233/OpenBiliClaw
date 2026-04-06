"""Dependency bootstrap for the OpenClaw adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from openbiliclaw.bilibili.api import BilibiliAPIClient
from openbiliclaw.bilibili.auth import resolve_runtime_cookie
from openbiliclaw.config import Config, load_config
from openbiliclaw.discovery.engine import ContentDiscoveryEngine
from openbiliclaw.discovery.strategies.strategies import (
    ExploreStrategy,
    RelatedChainStrategy,
    SearchStrategy,
    TrendingStrategy,
)
from openbiliclaw.llm import build_llm_registry
from openbiliclaw.llm.service import LLMService
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.runtime.account_sync import AccountSyncService
from openbiliclaw.runtime.refresh import ContinuousRefreshController
from openbiliclaw.soul.engine import SoulEngine
from openbiliclaw.storage.database import Database

from .operations import OpenClawAdapter


@dataclass(slots=True)
class OpenClawAdapterServices:
    """Shared services bundle used by the OpenClaw adapter."""

    config: Config | Any
    database: Database | Any
    memory_manager: MemoryManager | Any
    soul_engine: SoulEngine | Any
    llm_service: LLMService | Any
    bilibili_client: BilibiliAPIClient | Any
    discovery_engine: ContentDiscoveryEngine | Any
    recommendation_engine: RecommendationEngine | Any
    runtime_controller: ContinuousRefreshController | Any
    account_sync_service: AccountSyncService | Any


def build_openclaw_adapter_services() -> OpenClawAdapterServices:
    """Build the shared service bundle for the OpenClaw adapter."""
    config = load_config()
    llm_registry = build_llm_registry(config)

    database = Database(config.data_path / "openbiliclaw.db")
    database.initialize()

    memory_manager = MemoryManager(config.data_path, database=database)
    memory_manager.initialize()

    soul_engine = SoulEngine(
        llm=llm_registry,  # type: ignore[arg-type]
        memory=memory_manager,
    )
    llm_service = LLMService(registry=llm_registry, memory=memory_manager)
    from openbiliclaw.recommendation.curator import PoolCurator

    from openbiliclaw.llm.registry import build_embedding_service

    embedding_service = build_embedding_service(config, llm_registry)

    curator = PoolCurator(database)
    recommendation_engine = RecommendationEngine(
        llm=llm_service, database=database, curator=curator,
        embedding_service=embedding_service,
    )
    bilibili_client = BilibiliAPIClient(
        cookie=resolve_runtime_cookie(
            data_dir=config.data_path,
            configured_cookie=config.bilibili.cookie,
        )
    )

    from openbiliclaw.discovery.engine import DiscoveryConcurrencyController

    concurrency = DiscoveryConcurrencyController(
        bilibili_request_concurrency=4,
        llm_evaluation_concurrency=4,
    )

    discovery_engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=database,
        embedding_service=embedding_service,
        concurrency=concurrency,
    )
    search_strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=concurrency,
    )
    trending_strategy = TrendingStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        concurrency=concurrency,
    )
    related_strategy = RelatedChainStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        memory_manager=cast("Any", memory_manager),
        search_strategy=search_strategy,
        trending_strategy=trending_strategy,
        concurrency=concurrency,
    )
    explore_strategy = ExploreStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=concurrency,
    )
    discovery_engine.register_strategy(search_strategy)
    discovery_engine.register_strategy(trending_strategy)
    discovery_engine.register_strategy(related_strategy)
    discovery_engine.register_strategy(explore_strategy)

    runtime_controller = ContinuousRefreshController(
        memory_manager=memory_manager,
        database=database,
        soul_engine=soul_engine,
        discovery_engine=discovery_engine,
        recommendation_engine=recommendation_engine,
        pool_target_count=config.scheduler.pool_target_count,
    )
    account_sync_service = AccountSyncService(
        memory_manager=memory_manager,
        bilibili_client=bilibili_client,
        soul_engine=soul_engine,
        sync_interval_hours=config.scheduler.account_sync_interval_hours,
    )

    return OpenClawAdapterServices(
        config=config,
        database=database,
        memory_manager=memory_manager,
        soul_engine=soul_engine,
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        discovery_engine=discovery_engine,
        recommendation_engine=recommendation_engine,
        runtime_controller=runtime_controller,
        account_sync_service=account_sync_service,
    )


def build_openclaw_adapter() -> OpenClawAdapter:
    """Build a ready-to-use OpenClaw adapter."""
    return OpenClawAdapter(services=build_openclaw_adapter_services())
