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
  - ``extension_native_save_broker`` — owns durable extension save jobs
  - ``presence`` — tracks shared extension runtime-stream presence

**Swappable components** (rebuilt on hot-reload):
  - ``llm_registry``, ``llm_service``, ``bilibili_client``, ``saved_sync_service``
  - ``soul_engine``, ``dialogue``
  - ``discovery_engine``, ``recommendation_engine``
  - ``runtime_controller``, ``account_sync_service``
  - ``auto_update_service``
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from openbiliclaw.config import (
    llm_concurrency_from_config as _llm_concurrency_from_config,
)
from openbiliclaw.config import (
    publication_date_preference_for_source,
    source_date_preferences,
)
from openbiliclaw.runtime.presence import PresenceTracker
from openbiliclaw.runtime.presence import background_llm_work_allowed as _gate
from openbiliclaw.runtime.source_policy import effective_pool_source_shares
from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI

    from openbiliclaw.config import Config
    from openbiliclaw.soul.dialogue_learn_queue import (
        DialogueDispatcher,
        DialogueJobKind,
    )
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)
_BACKGROUND_TASK_CANCEL_TIMEOUT_SECONDS = 1.5
# Dialogue learning may legitimately spend up to the configured 20-minute LLM
# timeout in one worker job.  Give it the same 25-minute no-progress envelope
# as guided preference analysis instead of rolling config.toml back after 30s.
_DIALOGUE_SETTLEMENT_DRAIN_TIMEOUT_SECONDS = 25 * 60.0


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
    yt_date_preference = publication_date_preference_for_source(yt_cfg)
    budgets = strategy_unit_budget or {}
    scheduler = getattr(config, "scheduler", None)
    default_run_budget = max(1, int(getattr(scheduler, "discovery_limit", 30)))

    def _strategy_budget(strategy: str, attr: str) -> int:
        if strategy in budgets:
            return int(budgets[strategy])
        configured = int(getattr(yt_cfg, attr, 0))
        return default_run_budget if configured <= 0 else configured

    search_budget = _strategy_budget("yt_search", "daily_search_budget")
    trending_budget = _strategy_budget("yt_trending", "daily_trending_budget")
    channel_budget = _strategy_budget("yt_channel", "daily_channel_budget")
    return [
        YoutubeSearchStrategy(
            client=client,
            llm_service=llm_service,
            concurrency=concurrency,
            database=database,
            queries_per_run=max(0, search_budget),
            date_preference=yt_date_preference,
        ),
        YoutubeTrendingStrategy(
            client=client,
            llm_service=llm_service,
            concurrency=concurrency,
            database=database,
            fetch_limit=max(0, trending_budget),
            date_preference=yt_date_preference,
        ),
        YoutubeChannelStrategy(
            client=client,
            llm_service=llm_service,
            memory=memory,
            concurrency=concurrency,
            database=database,
            max_channels=max(0, channel_budget),
            date_preference=yt_date_preference,
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


def _build_dialogue_settlement_dispatcher(
    soul_engine: Any,
    api_handlers: Mapping[DialogueJobKind, DialogueDispatcher] | None = None,
) -> DialogueDispatcher:
    """Build the one typed dispatcher installed by the API runtime.

    Engine-owned settlement kinds are dispatched directly. API-owned card,
    pending-open, and durable-reply effects are supplied through the stable
    runtime handler façade so hot reload can replace the worker without
    capturing an HTTP request object.
    """
    from types import MappingProxyType

    from openbiliclaw.soul.dialogue_learn_queue import (
        AnchorAdmissionSnapshot,
        AnchorPersisted,
        DialogueDispatchReturn,
        DialogueJob,
        DialogueJobKind,
        DialogueJobResult,
    )

    async def _dispatch(job: DialogueJob) -> DialogueDispatchReturn:
        if job.kind is DialogueJobKind.LEARN:
            from openbiliclaw.llm.service import _background_admission_bypass

            payload = dict(job.payload)
            snapshot = job.effective_anchor_snapshot
            if isinstance(snapshot, AnchorPersisted):
                payload["anchor_ref"] = snapshot.ref
                payload["anchor_generation"] = snapshot.generation
            else:
                payload["anchor_ref"] = ""
                payload["anchor_generation"] = 0
            with _background_admission_bypass():
                await soul_engine.learn_from_dialogue(**payload)
            return DialogueJobResult(outcome="completed")
        if job.kind is DialogueJobKind.CARD_RECONCILE:
            handler = api_handlers.get(job.kind) if api_handlers is not None else None
            if handler is not None:
                return await handler(job)
            reconcile = getattr(soul_engine, "_apply_card_reconcile", None)
            if not callable(reconcile):
                raise RuntimeError("card.reconcile worker handler is not ready")
            result = await reconcile(ref=str(job.payload.get("ref", "")))
            if not isinstance(result, dict):
                raise RuntimeError("card.reconcile returned an invalid result")
            return DialogueJobResult(
                outcome=str(result.get("outcome", "completed")),
                settlement=MappingProxyType(dict(result)),
            )
        snapshot = cast("AnchorAdmissionSnapshot", job.effective_anchor_snapshot)
        if job.kind is DialogueJobKind.SETTLE_HYPOTHESIS:
            result = await soul_engine._apply_hypothesis_settlement(
                ref=str(job.payload.get("ref", "")),
                hypothesis=str(job.payload.get("hypothesis", "")),
                requested_verdict=str(job.payload.get("requested_verdict", "")),
                turn_id=str(job.payload.get("turn_id", "")),
                source=str(job.payload.get("source", "")),
                derived=[
                    dict(item)
                    for item in cast("list[dict[str, object]]", job.payload.get("derived", []))
                ],
                anchor_snapshot=snapshot,
            )
            return DialogueJobResult(
                outcome=str(result.get("outcome", "completed")),
                settlement=MappingProxyType(dict(result)),
            )
        if job.kind is DialogueJobKind.SETTLE_CONFUSION:
            result = await soul_engine._apply_confusion_settlement(
                ref=str(job.payload.get("ref", "")),
                requested_verdict=str(job.payload.get("requested_verdict", "")),
                note=str(job.payload.get("note", "")),
                turn_id=str(job.payload.get("turn_id", "")),
                source=str(job.payload.get("source", "")),
                anchor_snapshot=snapshot,
            )
            return DialogueJobResult(
                outcome=str(result.get("outcome", "completed")),
                settlement=MappingProxyType(dict(result)),
            )
        if job.kind is DialogueJobKind.CONFUSION_ATTRIBUTION_REPLAY:
            return cast(
                "DialogueDispatchReturn",
                await soul_engine._dispatch_confusion_attribution_replay(job),
            )
        handler = api_handlers.get(job.kind) if api_handlers is not None else None
        if handler is not None:
            return await handler(job)
        if job.kind in {
            DialogueJobKind.CARD_DEFER,
            DialogueJobKind.CARD_DISCUSS,
            DialogueJobKind.ANCHOR_ESTABLISH,
            DialogueJobKind.PROBE_REPLY_APPLY,
            DialogueJobKind.CONFUSION_REPLY_APPLY,
            DialogueJobKind.CONFUSION_OPEN_SYNC,
        }:
            raise RuntimeError(f"{job.kind.value} runtime handler is not installed")
        raise AssertionError(f"Unhandled dialogue settlement kind: {job.kind!r}")

    return _dispatch


def _build_account_sync_x_components(
    config: Any,
    database: Any,
) -> tuple[Any | None, Any | None]:
    """Build the client and credential-bound health store for scheduled X sync.

    Reuses ``resolve_x_cookie`` exactly as init/discovery do; returns
    ``(None, None)`` when the source is disabled or no cookie is available so
    account_sync's X path stays fully inert.
    """
    try:
        from openbiliclaw.api.source_auth.write import credential_fingerprint
        from openbiliclaw.sources.x_auth import resolve_x_cookie
        from openbiliclaw.sources.x_client import XClient
        from openbiliclaw.storage.x_health import XSourceHealthStore

        twitter_cfg = getattr(getattr(config, "sources", None), "twitter", None)
        if twitter_cfg is None or not bool(getattr(twitter_cfg, "enabled", False)):
            return None, None
        cookie_env = str(getattr(twitter_cfg, "cookie_env", "OPENBILICLAW_X_COOKIE"))
        cookie = resolve_x_cookie(data_dir=config.data_path, cookie_env=cookie_env)
        if not cookie:
            return None, None
        return (
            XClient(cookie=cookie),
            XSourceHealthStore(
                database,
                credential_fingerprint=credential_fingerprint("twitter", cookie),
            ),
        )
    except Exception:
        logger.debug("account_sync: X components construction skipped", exc_info=True)
        return None, None


def build_youtube_discovery_producer(
    *,
    config: Any,
    database: Any,
    soul_engine: Any,
    discovery_engine: Any,
    llm_service: Any,
    memory: Any,
    concurrency: Any,
    candidate_pipeline: Any | None = None,
    keyword_fetch: Any | None = None,
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
        queries: list[str] | None = None,
        keyword_ids: dict[str, int] | None = None,
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
        # Unified keyword planner injection (P1.7): forward claimed words to the
        # engine as ``keywords``; the engine maps them onto the strategy's
        # ``queries`` param (only ``yt_search`` declares it). ``None`` keeps the
        # legacy self-generating behavior byte-identical.
        inject: dict[str, Any] = {}
        if queries is not None:
            inject["keywords"] = list(queries)
        # P1.8 yield provenance: forward the keyword→id map so the engine stamps
        # each produced item's ``source_keyword_id`` for admit-time backfill.
        if keyword_ids:
            inject["keyword_ids"] = dict(keyword_ids)
        produce_fn = getattr(discovery_engine, "produce_candidates", None)
        if callable(produce_fn):
            raw_items = await produce_fn(
                profile,
                strategies=[strategy],
                limit=max(1, int(result_limit)),
                **inject,
            )
        else:
            raw_items = await discovery_engine.discover(
                profile,
                strategies=[strategy],
                limit=max(1, int(result_limit)),
                **inject,
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
        min_interval_minutes=int(getattr(yt_cfg, "min_interval_minutes", 3)),
        daily_search_budget=int(getattr(yt_cfg, "daily_search_budget", 0)),
        daily_trending_budget=int(getattr(yt_cfg, "daily_trending_budget", 0)),
        daily_channel_budget=int(getattr(yt_cfg, "daily_channel_budget", 0)),
        candidate_pipeline=candidate_pipeline,
        keyword_fetch=keyword_fetch,
    )


@dataclass
class RuntimeContext:
    """Mutable holder for all runtime components used by API endpoints."""

    # ── Stable (never rebuilt) ──────────────────────────────────────
    database: Any = None
    memory_manager: Any = None
    event_hub: Any = None
    # Stable, test-injectable execution bridge. Production adapter registration
    # is intentionally owned by the native-save runtime wiring layer.
    extension_native_save_broker: Any = None
    presence: PresenceTracker = field(default_factory=PresenceTracker)
    # v0.3.63+: tracks every detached ``asyncio.create_task`` spawned by
    # the runtime (refresh manual / per-strategy precompute, recommendation
    # engine classify+delight, prewarm helpers, per-event triggers). On
    # ``rebuild_from_config`` these are cancelled before new runtime objects
    # are constructed so old detached work doesn't compete with the freshly
    # built runtime for SQLite writes / LLM tokens.
    task_registry: BackgroundTaskRegistry = field(default_factory=BackgroundTaskRegistry)
    llm_concurrency_gate: Any = None
    pool_inventory_commit_callback: Any = field(init=False, repr=False, compare=False)
    _pool_inventory_commit_subscribers: list[Any] = field(
        default_factory=list,
        init=False,
        repr=False,
        compare=False,
    )
    # Lazily-built guided-init coordinator (gui-init spec §5). Not a constructor
    # arg; created on first access bound to THIS ctx so it always reads the
    # current database / runtime_controller even after a hot-reload swaps them
    # (review R2 A-1). All three construct paths inherit it via the property.
    _init_coordinator: Any = field(default=None, init=False, repr=False, compare=False)
    _init_prereqs: Any = field(default=None, init=False, repr=False, compare=False)

    # ── Swappable (rebuilt on hot-reload) ───────────────────────────
    config: Any = None
    degraded: bool = False
    degraded_reason: str = ""
    degraded_issues: list[Any] = field(default_factory=list)
    llm_registry: Any = None
    llm_service: Any = None
    bilibili_client: Any = None
    bangumi_client: Any = None
    github_client: Any = None
    v2ex_client: Any = None
    weibo_client: Any = None
    saved_sync_service: Any = None
    soul_engine: Any = None
    dialogue: Any = None
    # Wave 1: the one self-owned typed dialogue settlement queue. It is not in
    # cancel_all and uses pause/drain + exact permit handoff on hot reload.
    dialogue_settlement_queue: Any = None
    dialogue_settlement_handlers: dict[Any, Any] = field(default_factory=dict)
    dialogue_settlement_guard: Any = None
    discovery_engine: Any = None
    recommendation_engine: Any = None
    runtime_controller: Any = None
    account_sync_service: Any = None
    auto_update_service: Any = None

    def __post_init__(self) -> None:
        """Initialize stable callbacks and local-only saved-list behavior."""
        self.pool_inventory_commit_callback = self._handle_pool_inventory_commit
        if self.dialogue_settlement_guard is None:
            from openbiliclaw.soul.dialogue_settlement_guard import DialogueSettlementGuard

            self.dialogue_settlement_guard = DialogueSettlementGuard()

        if self.database is None:
            return
        from openbiliclaw.saved_sync.adapters.extension import (
            build_extension_native_save_adapters,
        )
        from openbiliclaw.saved_sync.extension_broker import ExtensionNativeSaveBroker
        from openbiliclaw.saved_sync.router import NativeSaveRouter
        from openbiliclaw.saved_sync.service import SavedSyncService

        if self.extension_native_save_broker is None:

            async def wake_platform(platform_slug: str) -> None:
                publish = getattr(self.event_hub, "publish", None)
                if callable(publish):
                    with suppress(Exception):
                        await publish({"type": f"{platform_slug}_task_available"})

            self.extension_native_save_broker = ExtensionNativeSaveBroker(
                self.database,
                wake_platform=wake_platform,
            )
        if self.saved_sync_service is None:
            self.saved_sync_service = SavedSyncService(
                self.database,
                NativeSaveRouter(
                    build_extension_native_save_adapters(self.extension_native_save_broker)
                ),
                task_starter=lambda name, coro: self.task_registry.track(name, coro),
            )

    def add_pool_inventory_commit_subscriber(self, callback: Any) -> None:
        """Register a stable post-commit observer once for this context."""
        if callback not in self._pool_inventory_commit_subscribers:
            self._pool_inventory_commit_subscribers.append(callback)

    async def _handle_pool_inventory_commit(
        self,
        counts: dict[str, int] | None = None,
    ) -> None:
        """Synchronize committed inventory, reusing serve counts when supplied."""
        controller = self.runtime_controller
        if counts is not None and "available" in counts:
            update_inventory = getattr(controller, "_update_llm_inventory_state", None)
            if callable(update_inventory):
                update_inventory(max(0, int(counts.get("available", 0))))
            else:
                update = getattr(self.llm_concurrency_gate, "update_inventory", None)
                if callable(update):
                    update(
                        available=max(0, int(counts.get("available", 0))),
                        target=max(0, int(getattr(controller, "pool_target_count", 0))),
                    )
        else:
            readiness = getattr(controller, "_pool_readiness_counts", None)
            if callable(readiness):
                try:
                    result = readiness()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.exception("post-commit inventory synchronization failed")
            else:
                self._sync_inventory_without_controller()

        for callback in tuple(self._pool_inventory_commit_subscribers):
            try:
                signature = inspect.signature(callback)
                accepts_counts = any(
                    parameter.kind
                    in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                    for parameter in signature.parameters.values()
                ) or any(
                    parameter.kind is inspect.Parameter.VAR_POSITIONAL
                    for parameter in signature.parameters.values()
                )
            except (TypeError, ValueError):
                accepts_counts = False
            try:
                result = callback(counts) if accepts_counts else callback()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("post-commit inventory subscriber failed")

    def _sync_inventory_without_controller(self) -> None:
        count_pool = getattr(self.database, "count_pool_candidates", None)
        update = getattr(self.llm_concurrency_gate, "update_inventory", None)
        if not callable(count_pool) or not callable(update):
            return
        try:
            nickname = ""
            load_state = getattr(self.memory_manager, "load_discovery_runtime_state", None)
            if callable(load_state):
                state = load_state()
                info = state.get("xhs_self_info", {}) if isinstance(state, dict) else {}
                if isinstance(info, dict):
                    nickname = str(info.get("nickname", "") or "").strip()
            try:
                available = int(count_pool(xhs_self_nickname=nickname))
            except TypeError:
                available = int(count_pool())
            controller_target = getattr(self.runtime_controller, "pool_target_count", None)
            scheduler = getattr(getattr(self, "config", None), "scheduler", None)
            target = (
                controller_target
                if controller_target is not None
                else getattr(scheduler, "pool_target_count", 0)
            )
            update(available=max(0, available), target=max(0, int(target)))
        except Exception:
            logger.exception("post-commit inventory fallback synchronization failed")

    @property
    def init_coordinator(self) -> Any:
        """Guided-init coordinator bound to this ctx (lazy singleton, spec §5)."""
        if self._init_coordinator is None:
            from openbiliclaw.runtime.init_coordinator import InitCoordinator

            self._init_coordinator = InitCoordinator(self)
        return self._init_coordinator

    @property
    def init_prereqs(self) -> Any:
        """Cached guided-init prerequisite probes bound to this ctx (spec §3)."""
        if self._init_prereqs is None:
            from openbiliclaw.runtime.init_prereqs import InitPrereqs

            self._init_prereqs = InitPrereqs(self)
        return self._init_prereqs

    def background_llm_work_allowed(self) -> bool:
        """Return whether daemon-owned background LLM / embedding work may run.

        Before the first full profile exists, and while a guided init is active,
        ALL daemon-owned background loops (account_sync, continuous refresh,
        soul pipeline ticks) pause.  This keeps account-sync from fetching and
        analysing the same bootstrap history before the user presses Start,
        then racing or duplicating guided init's explicit analyze/build/backfill
        work. Init's own work bypasses this gate — it calls ``soul_engine`` /
        ``run_init_backfill`` directly, neither of which consults
        ``llm_work_allowed``.
        """
        try:
            if self.database is not None and self.init_coordinator.init_active():
                return False
        except Exception:
            pass
        try:
            profile_ready = getattr(getattr(self, "soul_engine", None), "is_profile_ready", None)
            if callable(profile_ready) and not bool(profile_ready()):
                return False
        except Exception:
            # A broken readiness read is not permission to start background LLM
            # work against an unknown profile state.  Guided init remains
            # available because its explicit calls bypass this predicate.
            return False
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
        # Pause/drain the old self-owned queue, then revoke its exact permit
        # before any new worker may register. Construction failure gives the
        # drained old Task a fresh nonce before it resumes.
        old_settlement_queue = self.dialogue_settlement_queue
        old_permit_revoked = False
        if old_settlement_queue is not None:
            try:
                await old_settlement_queue.pause_and_drain(
                    timeout=_DIALOGUE_SETTLEMENT_DRAIN_TIMEOUT_SECONDS
                )
            except (Exception, asyncio.CancelledError):
                with suppress(Exception):
                    old_settlement_queue.resume()
                logger.warning(
                    "Hot-reload aborted: old dialogue settlement queue did not drain",
                    exc_info=True,
                )
                raise
            await old_settlement_queue.wait_until_started()
            if not old_settlement_queue.revoke_worker_permit():
                old_settlement_queue.resume()
                raise RuntimeError("Failed to revoke old dialogue settlement worker permit")
            old_permit_revoked = True

        try:
            # Keep a running guided-init task alive across rebuild — config
            # writes are gated during init, but this exemption prevents an
            # in-flight init from being silently cancelled.
            cancelled = await self.task_registry.cancel_all(exclude=frozenset({"guided_init"}))
            if cancelled:
                logger.info(
                    "Hot-reload: cancelled %d background task(s) before rebuild",
                    cancelled,
                )
            self._rebuild_components(new_config)
            new_settlement_queue = self.dialogue_settlement_queue
            if new_settlement_queue is not None:
                await new_settlement_queue.wait_until_started()
        except (Exception, asyncio.CancelledError):
            # Atomic component construction leaves the old runtime installed.
            # Its permit was revoked, so rollback must allocate a fresh nonce.
            if old_settlement_queue is not None:
                with suppress(Exception):
                    if old_permit_revoked:
                        old_settlement_queue.reauthorize_worker()
                    old_settlement_queue.resume()
            raise
        else:
            # New permit is current. Old shutdown/finally can only compare and
            # clear its revoked tuple, so it cannot revoke the new worker.
            if old_settlement_queue is not None:
                with suppress(Exception):
                    await old_settlement_queue.shutdown(
                        timeout=_DIALOGUE_SETTLEMENT_DRAIN_TIMEOUT_SECONDS
                    )

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
            RECENT_SUPPLY_LANE_PAGE_SIZE,
            RECENT_SUPPLY_LANE_QUERIES,
            ExploreStrategy,
            RelatedChainStrategy,
            SearchStrategy,
            TrendingStrategy,
        )
        from openbiliclaw.llm import build_llm_registry
        from openbiliclaw.llm.concurrency import LLMConcurrencyGate, background_llm_concurrency
        from openbiliclaw.llm.registry import build_embedding_service
        from openbiliclaw.llm.service import LLMService, module_overrides_from_config
        from openbiliclaw.llm.usage_recorder import UsageRecorder
        from openbiliclaw.recommendation.engine import RecommendationEngine
        from openbiliclaw.runtime.account_sync import AccountSyncService
        from openbiliclaw.runtime.refresh import ContinuousRefreshController
        from openbiliclaw.runtime.source_incremental_sync import SourceIncrementalSync
        from openbiliclaw.runtime.updater import AutoUpdateService
        from openbiliclaw.saved_sync.adapters.bilibili import BilibiliNativeSaveAdapter
        from openbiliclaw.saved_sync.adapters.extension import (
            build_extension_native_save_adapters,
        )
        from openbiliclaw.saved_sync.router import NativeSaveRouter
        from openbiliclaw.saved_sync.service import SavedSyncService
        from openbiliclaw.soul.dialogue import SocraticDialogue
        from openbiliclaw.soul.engine import SoulEngine

        # 1. LLM layer (with usage ledger so ``openbiliclaw cost`` has data)
        new_registry = build_llm_registry(new_config)
        new_usage_recorder = UsageRecorder(sink=self.database)
        new_module_overrides = module_overrides_from_config(new_config)
        llm_concurrency = _llm_concurrency_from_config(new_config)
        new_llm_gate = self.llm_concurrency_gate or LLMConcurrencyGate(llm_concurrency)
        new_inventory_available: int | None = None
        count_pool = getattr(self.database, "count_pool_candidates", None)
        if callable(count_pool):
            try:
                state = self.memory_manager.load_discovery_runtime_state()
                info = state.get("xhs_self_info", {}) if isinstance(state, dict) else {}
                nickname = str(info.get("nickname", "")) if isinstance(info, dict) else ""
                available = int(count_pool(xhs_self_nickname=nickname))
            except (AttributeError, TypeError):
                available = int(count_pool())
            new_inventory_available = max(0, available)
        new_llm_service = LLMService(
            registry=new_registry,
            memory=self.memory_manager,
            usage_recorder=new_usage_recorder,
            module_overrides=new_module_overrides,
            concurrency=llm_concurrency,
            concurrency_gate=new_llm_gate,
        )

        # 2. Bilibili client
        new_bilibili_client = BilibiliAPIClient(
            cookie=resolve_runtime_cookie(
                data_dir=new_config.data_path,
                configured_cookie=new_config.bilibili.cookie,
            ),
            proxy=new_config.bilibili.proxy or None,
        )
        bangumi_cfg = getattr(getattr(new_config, "sources", None), "bangumi", None)
        new_bangumi_client: Any = None
        if bool(getattr(bangumi_cfg, "enabled", False)):
            from openbiliclaw.sources.bangumi_client import BangumiClient

            new_bangumi_client = BangumiClient(
                access_token=str(getattr(bangumi_cfg, "access_token", "") or "") or None,
                request_interval_seconds=float(
                    getattr(bangumi_cfg, "request_interval_seconds", 1.0)
                ),
            )
        github_cfg = getattr(getattr(new_config, "sources", None), "github", None)
        new_github_client: Any = None
        github_access_token = ""
        if bool(getattr(github_cfg, "enabled", False)):
            from openbiliclaw.sources.github_client import (
                GitHubClient,
                resolve_github_access_token,
            )

            github_access_token, _token_origin = resolve_github_access_token(
                config_token=str(getattr(github_cfg, "access_token", "") or ""),
                # This is a fixed security boundary. Do not honor arbitrary
                # renamed variables, GITHUB_TOKEN, or GH_TOKEN here.
                token_env="OPENBILICLAW_GITHUB_TOKEN",
            )
            new_github_client = GitHubClient(
                token=github_access_token or None,
                request_interval_seconds=float(
                    getattr(github_cfg, "request_interval_seconds", 6.0)
                ),
            )
        v2ex_cfg = getattr(getattr(new_config, "sources", None), "v2ex", None)
        new_v2ex_client: Any = None
        v2ex_access_token = ""
        if bool(getattr(v2ex_cfg, "enabled", False)):
            from openbiliclaw.sources.v2ex_client import V2EXClient

            token_env = str(
                getattr(v2ex_cfg, "token_env", "OPENBILICLAW_V2EX_TOKEN")
                or "OPENBILICLAW_V2EX_TOKEN"
            ).strip()
            v2ex_access_token = (
                str(os.environ.get(token_env, "") or "").strip()
                or str(getattr(v2ex_cfg, "access_token", "") or "").strip()
            )
            new_v2ex_client = V2EXClient(
                access_token=v2ex_access_token or None,
                request_interval_seconds=float(getattr(v2ex_cfg, "request_interval_seconds", 2.0)),
            )
        weibo_cfg = getattr(getattr(new_config, "sources", None), "weibo", None)
        new_weibo_client: Any = None
        if bool(getattr(weibo_cfg, "enabled", False)):
            from openbiliclaw.sources.weibo_client import WeiboClient

            new_weibo_client = WeiboClient(
                request_interval_seconds=float(getattr(weibo_cfg, "request_interval_seconds", 3.0))
            )
        new_saved_sync_service = SavedSyncService(
            self.database,
            NativeSaveRouter(
                (
                    *build_extension_native_save_adapters(self.extension_native_save_broker),
                    BilibiliNativeSaveAdapter(new_bilibili_client),
                )
            ),
            task_starter=lambda name, coro: self.task_registry.track(name, coro),
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
            preference_prompt_view=str(getattr(soul_cfg, "preference_prompt_view", "legacy")),
            awareness_prompt_view=str(getattr(soul_cfg, "awareness_prompt_view", "compact-v1")),
            insight_prompt_view=str(getattr(soul_cfg, "insight_prompt_view", "legacy")),
            awareness_event_batch_size=int(getattr(soul_cfg, "awareness_event_batch_size", 300)),
            insight_note_batch_size=int(getattr(soul_cfg, "insight_note_batch_size", 150)),
            cognition_max_tokens=int(getattr(soul_cfg, "cognition_max_tokens", 32768)),
            posture_gate_mode=str(getattr(soul_cfg, "posture_gate_mode", "shadow")),
            posture_gate_force_enforce=bool(getattr(soul_cfg, "posture_gate_force_enforce", False)),
            module_overrides=new_module_overrides,
            llm_concurrency=llm_concurrency,
            llm_concurrency_gate=new_llm_gate,
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
            profile_consolidation_enabled=bool(
                getattr(new_config.scheduler, "profile_consolidation_enabled", True)
            ),
            profile_consolidation_interval_hours=int(
                getattr(new_config.scheduler, "profile_consolidation_interval_hours", 12)
            ),
            profile_consolidation_like_target_upper=int(
                getattr(new_config.scheduler, "profile_consolidation_like_target_upper", 512)
            ),
            profile_consolidation_like_target_soft=int(
                getattr(new_config.scheduler, "profile_consolidation_like_target_soft", 450)
            ),
            profile_consolidation_archive_enabled=bool(
                getattr(new_config.scheduler, "profile_consolidation_archive_enabled", True)
            ),
            feedback_batch_threshold=int(
                getattr(new_config.scheduler, "feedback_batch_threshold", 3)
            ),
            unified_interest_line=bool(
                getattr(new_config.scheduler, "unified_interest_line", False)
            ),
            database=self.database,
        )

        # 4. Embedding service
        new_embedding_service = build_embedding_service(new_config, new_registry)

        # 5. Share embedding with soul pipeline for semantic purges
        set_emb = getattr(new_soul_engine, "set_embedding_service", None)
        if callable(set_emb):
            set_emb(new_embedding_service)

        # 6. Recommendation engine
        from openbiliclaw.recommendation.curator import PoolCurator

        publication_preference = publication_date_preference_for_source(
            getattr(getattr(new_config, "sources", None), "bilibili", None)
        )
        set_publication_preference = getattr(self.database, "set_publication_date_preference", None)
        if callable(set_publication_preference):
            set_publication_preference(publication_preference)
        set_source_preferences = getattr(
            self.database, "set_source_publication_date_preferences", None
        )
        if callable(set_source_preferences):
            set_source_preferences(source_date_preferences(new_config))
        new_curator = PoolCurator(
            self.database,
            publication_preference=publication_preference,
        )

        def _xhs_self_info_provider() -> dict[str, object] | None:
            state = self.memory_manager.load_discovery_runtime_state()
            info = state.get("xhs_self_info")
            return info if isinstance(info, dict) else None

        configured_copy_target = max(
            0,
            int(getattr(new_config.scheduler, "copy_ready_target_count", 0) or 0),
        )
        effective_copy_target = min(
            configured_copy_target,
            max(0, int(getattr(new_config.scheduler, "pool_target_count", 0) or 0)),
        )
        from openbiliclaw.runtime.serve_snapshot import ServeSnapshotStore

        new_recommendation_engine = RecommendationEngine(
            llm=new_llm_service,
            database=self.database,
            curator=new_curator,
            embedding_service=new_embedding_service,
            task_registry=self.task_registry,
            xhs_self_info_provider=_xhs_self_info_provider,
            copy_ready_target_count=effective_copy_target,
            pool_available_target_count=max(
                0,
                int(getattr(new_config.scheduler, "pool_target_count", 0) or 0),
            ),
            visual_profile_enabled=bool(
                getattr(getattr(new_config, "discovery", None), "visual_profile_enabled", False)
            ),
            keyframe_enabled=bool(
                getattr(getattr(new_config, "discovery", None), "keyframe_enabled", False)
            ),
            keyframe_max_frames=int(
                getattr(getattr(new_config, "discovery", None), "keyframe_max_frames", 4)
            ),
            keyframe_fetch_limit=int(
                getattr(getattr(new_config, "discovery", None), "keyframe_fetch_limit", 50)
            ),
            danmaku_enabled=bool(
                getattr(getattr(new_config, "discovery", None), "danmaku_enabled", False)
            ),
            danmaku_fetch_limit=int(
                getattr(getattr(new_config, "discovery", None), "danmaku_fetch_limit", 50)
            ),
            danmaku_max_chars=int(
                getattr(getattr(new_config, "discovery", None), "danmaku_max_chars", 500)
            ),
            bilibili_client=new_bilibili_client,
            serve_snapshot_store=ServeSnapshotStore(
                new_config.data_path / "runtime" / "serve_snapshot.json"
            ),
        )

        discovery_cfg = getattr(new_config, "discovery", None)

        # P1.7: unified keyword planner FETCH coordinator — claim-from-store +
        # word-lifecycle helper shared by B站 search / explore and external
        # search producers. Holds the keyword-store DAO (the database) +
        # discovery config (the flag + ``fetch_batch``). With the flag off every
        # site's ``should_claim`` returns False, so wiring it in is inert.
        from openbiliclaw.config import DiscoveryConfig
        from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator

        new_keyword_fetch = KeywordFetchCoordinator(
            database=self.database,
            # Real ``Config`` always carries ``discovery`` (a dataclass field);
            # lightweight test stubs (SimpleNamespace) may not — fall back to the
            # default (flag off) so the coordinator stays inert.
            discovery_config=discovery_cfg or DiscoveryConfig(),
        )

        # 7. Discovery engine + strategies
        concurrency = DiscoveryConcurrencyController(
            bilibili_request_concurrency=2,
            llm_evaluation_concurrency=background_llm_concurrency(llm_concurrency),
        )
        new_discovery_engine = ContentDiscoveryEngine(
            llm_service=new_llm_service,
            database=self.database,
            concurrency=concurrency,
            embedding_service=new_embedding_service,
            multimodal_evaluation_enabled=bool(
                getattr(discovery_cfg, "multimodal_evaluation_enabled", False)
            ),
            multimodal_batch_size=int(getattr(discovery_cfg, "multimodal_batch_size", 8)),
            multimodal_image_max_px=int(getattr(discovery_cfg, "multimodal_image_max_px", 384)),
            multimodal_image_quality=int(getattr(discovery_cfg, "multimodal_image_quality", 72)),
            multimodal_image_timeout_seconds=(
                int(getattr(discovery_cfg, "multimodal_image_timeout_seconds", 6))
            ),
            eval_prefilter_mode=str(getattr(discovery_cfg, "eval_prefilter_mode", "shadow")),
        )
        search_strategy = SearchStrategy(
            llm_service=new_llm_service,
            bilibili_client=new_bilibili_client,
            concurrency=concurrency,
            database=self.database,
            embedding_service=new_embedding_service,
            publication_preference=publication_preference,
            recent_lane_queries_per_run=RECENT_SUPPLY_LANE_QUERIES,
            recent_lane_page_size=RECENT_SUPPLY_LANE_PAGE_SIZE,
        )
        trending_strategy = TrendingStrategy(
            bilibili_client=new_bilibili_client,
            llm_service=new_llm_service,
            concurrency=concurrency,
            database=self.database,
            embedding_service=new_embedding_service,
            date_preference=publication_preference,
        )
        related_strategy = RelatedChainStrategy(
            bilibili_client=new_bilibili_client,
            llm_service=new_llm_service,
            memory_manager=cast("Any", self.memory_manager),
            search_strategy=search_strategy,
            trending_strategy=trending_strategy,
            concurrency=concurrency,
            database=self.database,
            date_preference=publication_preference,
        )
        explore_strategy = ExploreStrategy(
            llm_service=new_llm_service,
            bilibili_client=new_bilibili_client,
            concurrency=concurrency,
            embedding_service=new_embedding_service,
            database=cast("Any", self.database),
            keyword_fetch=new_keyword_fetch,
            date_preference=publication_preference,
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

        # Register X (Twitter) adapter — server-side cookie replay, like
        # Bilibili / Douyin-direct (a real fetch(), NOT an extension stub).
        # Gated on [sources.twitter].enabled. The branch is the ONLY place
        # twitter_cli / x_client are imported, so non-X installs (where the
        # optional ``openbiliclaw[x]`` extra is absent) never touch them.
        twitter_cfg = getattr(getattr(new_config, "sources", None), "twitter", None)
        new_x_client: object | None = None
        if twitter_cfg is not None and bool(getattr(twitter_cfg, "enabled", False)):
            from openbiliclaw.discovery.strategies.x import (
                XCreatorStrategy,
                XForYouStrategy,
                XSearchStrategy,
            )
            from openbiliclaw.sources.twitter_adapter import XAdapter
            from openbiliclaw.sources.x_auth import resolve_x_cookie
            from openbiliclaw.sources.x_client import XClient

            x_cookie = resolve_x_cookie(
                data_dir=new_config.data_path,
                cookie_env=str(getattr(twitter_cfg, "cookie_env", "OPENBILICLAW_X_COOKIE")),
            )
            x_client = XClient(cookie=x_cookie)
            new_x_client = x_client
            twitter_adapter = XAdapter(
                client=x_client,
                search=XSearchStrategy(client=x_client, llm_service=new_llm_service),
                feed=XForYouStrategy(client=x_client),
                creator=XCreatorStrategy(client=x_client),
            )
            new_discovery_engine.register_adapter(twitter_adapter)

        # 8. Continuous refresh controller
        from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline

        discovery_cfg = getattr(new_config, "discovery", None)
        admission_min_score = float(getattr(discovery_cfg, "admission_min_score", 0.60) or 0.60)
        set_admission_min_score = getattr(self.database, "set_admission_min_score", None)
        if callable(set_admission_min_score):
            set_admission_min_score(admission_min_score)
        new_candidate_pipeline = DiscoveryCandidatePipeline(
            database=self.database,
            discovery_engine=new_discovery_engine,
            pool_target_count=new_config.scheduler.pool_target_count,
            admission_min_score=admission_min_score,
            min_eval_batch_size=int(getattr(new_config.scheduler, "eval_min_batch_size", 15)),
            max_eval_wait_seconds=float(
                getattr(new_config.scheduler, "eval_max_wait_seconds", 90.0)
            ),
            candidate_fetch_oversample=4,
            xhs_self_nickname_provider=lambda: str(
                (_xhs_self_info_provider() or {}).get("nickname", "") or ""
            ).strip(),
        )
        new_bilibili_producer: Any = None
        new_xhs_producer: Any = None
        new_douyin_producer: Any = None
        new_youtube_producer: Any = None
        new_x_producer: Any = None
        new_zhihu_producer: Any = None
        new_reddit_producer: Any = None
        new_bangumi_producer: Any = None
        new_github_producer: Any = None
        new_linuxdo_producer: Any = None
        new_v2ex_producer: Any = None
        new_weibo_producer: Any = None
        if hasattr(self.database, "conn"):
            from openbiliclaw.runtime.bilibili_producer import BilibiliExtensionSearchProducer
            from openbiliclaw.runtime.xhs_producer import XhsTaskProducer
            from openbiliclaw.sources.bili_tasks import BiliTaskQueue
            from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

            bili_cfg = getattr(new_config.sources, "bilibili", None)
            xhs_cfg = getattr(new_config.sources, "xiaohongshu", None)
            sched_cfg = getattr(new_config, "scheduler", None)
            bili_enabled = bool(getattr(bili_cfg, "enabled", True)) and bool(
                getattr(sched_cfg, "enabled", True)
            )
            xhs_enabled = bool(getattr(xhs_cfg, "enabled", False)) and bool(
                getattr(sched_cfg, "enabled", True)
            )

            async def _kick_bili_extension() -> None:
                publish = getattr(getattr(self, "event_hub", None), "publish", None)
                if callable(publish):
                    with suppress(Exception):
                        await publish({"type": "bili_task_available", "source": "task_kick"})

            new_bilibili_producer = BilibiliExtensionSearchProducer(
                task_queue=BiliTaskQueue(self.database),
                soul_engine=new_soul_engine,
                llm_service=new_llm_service,
                bilibili_client=new_bilibili_client,
                presence=self.presence,
                enabled=bili_enabled,
                daily_budget=int(getattr(bili_cfg, "daily_search_budget", 0)),
                min_interval_minutes=int(getattr(bili_cfg, "min_interval_minutes", 3)),
                keywords_per_cycle=int(getattr(bili_cfg, "keywords_per_cycle", 3)),
                page_size=int(getattr(bili_cfg, "page_size", 20)),
                recent_lane_tasks_per_cycle=RECENT_SUPPLY_LANE_QUERIES,
                recent_lane_page_size=RECENT_SUPPLY_LANE_PAGE_SIZE,
                publication_preference=publication_preference,
                presence_grace_seconds=int(
                    getattr(sched_cfg, "extension_disconnect_grace_seconds", 90)
                ),
                candidate_pipeline=new_candidate_pipeline,
                keyword_fetch=new_keyword_fetch,
                kick=_kick_bili_extension,
            )
            new_xhs_producer = XhsTaskProducer(
                task_queue=XhsTaskQueue(self.database),
                soul_engine=new_soul_engine,
                llm_service=new_llm_service,
                enabled=xhs_enabled,
                daily_budget=int(getattr(xhs_cfg, "daily_search_budget", 20)),
                min_interval_minutes=int(getattr(xhs_cfg, "min_interval_minutes", 20)),
                keyword_fetch=new_keyword_fetch,
            )
            from openbiliclaw.runtime.douyin_producer import build_douyin_discovery_producer

            new_douyin_producer = build_douyin_discovery_producer(
                config=new_config,
                database=self.database,
                soul_engine=new_soul_engine,
                discovery_engine=new_discovery_engine,
                candidate_pipeline=new_candidate_pipeline,
                keyword_fetch=new_keyword_fetch,
                presence=self.presence,
                presence_grace_seconds=int(
                    getattr(sched_cfg, "extension_disconnect_grace_seconds", 90)
                ),
            )
            new_youtube_producer = build_youtube_discovery_producer(
                config=new_config,
                database=self.database,
                soul_engine=new_soul_engine,
                discovery_engine=new_discovery_engine,
                candidate_pipeline=new_candidate_pipeline,
                llm_service=new_llm_service,
                memory=cast("Any", self.memory_manager),
                concurrency=concurrency,
                keyword_fetch=new_keyword_fetch,
            )
            # X (Twitter) producer — fetch-only; enqueues into discovery_candidates
            # and never evaluates / writes content_cache (unified-pool spec). Gated
            # on [sources.twitter].enabled; the disabled path imports no twitter_cli.
            from openbiliclaw.runtime.x_producer import build_x_discovery_producer

            new_x_producer = build_x_discovery_producer(
                config=new_config,
                database=self.database,
                soul_engine=new_soul_engine,
                llm_service=new_llm_service,
                candidate_pipeline=new_candidate_pipeline,
                keyword_fetch=new_keyword_fetch,
            )
            from openbiliclaw.runtime.zhihu_producer import build_zhihu_discovery_producer

            async def _kick_zhihu_extension() -> None:
                publish = getattr(getattr(self, "event_hub", None), "publish", None)
                if callable(publish):
                    with suppress(Exception):
                        await publish({"type": "zhihu_task_available", "source": "task_kick"})

            new_zhihu_producer = build_zhihu_discovery_producer(
                config=new_config,
                database=self.database,
                soul_engine=new_soul_engine,
                candidate_pipeline=new_candidate_pipeline,
                keyword_fetch=new_keyword_fetch,
                kick=_kick_zhihu_extension,
            )
            from openbiliclaw.runtime.linuxdo_producer import (
                build_linuxdo_discovery_producer,
            )

            async def _kick_linuxdo_extension() -> None:
                publish = getattr(getattr(self, "event_hub", None), "publish", None)
                if callable(publish):
                    with suppress(Exception):
                        await publish({"type": "linuxdo_task_available", "source": "task_kick"})

            new_linuxdo_producer = build_linuxdo_discovery_producer(
                config=new_config,
                database=self.database,
                soul_engine=new_soul_engine,
                candidate_pipeline=new_candidate_pipeline,
                keyword_fetch=new_keyword_fetch,
                kick=_kick_linuxdo_extension,
            )
            from openbiliclaw.runtime.reddit_producer import build_reddit_discovery_producer

            new_reddit_producer = build_reddit_discovery_producer(
                config=new_config,
                database=self.database,
                soul_engine=new_soul_engine,
                candidate_pipeline=new_candidate_pipeline,
                keyword_fetch=new_keyword_fetch,
            )
            if new_bangumi_client is not None:
                from openbiliclaw.runtime.bangumi_producer import BangumiDiscoveryProducer

                new_bangumi_producer = BangumiDiscoveryProducer(
                    database=self.database,
                    soul_engine=new_soul_engine,
                    client=new_bangumi_client,
                    access_token=str(getattr(bangumi_cfg, "access_token", "") or ""),
                    enabled=bool(getattr(bangumi_cfg, "enabled", False))
                    and bool(getattr(sched_cfg, "enabled", True)),
                    subject_types=tuple(
                        getattr(bangumi_cfg, "subject_types", ("anime", "book", "game"))
                    ),
                    source_modes=tuple(
                        getattr(bangumi_cfg, "source_modes", ("search", "ranked", "latest"))
                    ),
                    daily_search_budget=int(getattr(bangumi_cfg, "daily_search_budget", 300)),
                    daily_ranked_budget=int(getattr(bangumi_cfg, "daily_ranked_budget", 100)),
                    daily_latest_budget=int(getattr(bangumi_cfg, "daily_latest_budget", 100)),
                    min_interval_minutes=int(getattr(bangumi_cfg, "min_interval_minutes", 3)),
                    candidate_pipeline=new_candidate_pipeline,
                    keyword_fetch=new_keyword_fetch,
                )
            if new_github_client is not None:
                from openbiliclaw.runtime.github_producer import GitHubDiscoveryProducer

                new_github_producer = GitHubDiscoveryProducer(
                    database=self.database,
                    soul_engine=new_soul_engine,
                    client=new_github_client,
                    access_token=github_access_token,
                    enabled=bool(getattr(github_cfg, "enabled", False))
                    and bool(getattr(sched_cfg, "enabled", True)),
                    source_modes=tuple(
                        getattr(github_cfg, "source_modes", ("search", "ranked", "latest"))
                    ),
                    daily_search_budget=int(getattr(github_cfg, "daily_search_budget", 120)),
                    daily_ranked_budget=int(getattr(github_cfg, "daily_ranked_budget", 60)),
                    daily_latest_budget=int(getattr(github_cfg, "daily_latest_budget", 60)),
                    min_interval_minutes=int(getattr(github_cfg, "min_interval_minutes", 10)),
                    candidate_pipeline=new_candidate_pipeline,
                    keyword_fetch=new_keyword_fetch,
                )
            if new_v2ex_client is not None:
                from openbiliclaw.api.source_auth.probe_cache import LIVE_PROBES
                from openbiliclaw.runtime.v2ex_producer import (
                    V2EXDiscoveryProducer,
                    build_v2ex_external_search_provider,
                )
                from openbiliclaw.sources.v2ex_identity import resolve_v2ex_identity_state

                v2ex_identity = resolve_v2ex_identity_state(
                    cfg=new_config,
                    database=self.database,
                    probes=LIVE_PROBES,
                )
                new_v2ex_producer = V2EXDiscoveryProducer(
                    database=self.database,
                    soul_engine=new_soul_engine,
                    client=new_v2ex_client,
                    access_token=v2ex_access_token,
                    identity_username=(
                        v2ex_identity.username if v2ex_identity.account_bootstrap_allowed else ""
                    ),
                    enabled=bool(getattr(v2ex_cfg, "enabled", False))
                    and bool(getattr(sched_cfg, "enabled", True)),
                    source_modes=tuple(
                        getattr(
                            v2ex_cfg, "source_modes", ("search", "node", "tab", "hot", "latest")
                        )
                    ),
                    tab_modes=tuple(getattr(v2ex_cfg, "tab_modes", ("tech", "creative", "qna"))),
                    node_allowlist=tuple(getattr(v2ex_cfg, "node_allowlist", ())),
                    node_blocklist=tuple(getattr(v2ex_cfg, "node_blocklist", ("sandbox",))),
                    node_downweight=tuple(getattr(v2ex_cfg, "node_downweight", ())),
                    daily_search_budget=int(getattr(v2ex_cfg, "daily_search_budget", 120)),
                    daily_node_budget=int(getattr(v2ex_cfg, "daily_node_budget", 180)),
                    daily_tab_budget=int(getattr(v2ex_cfg, "daily_tab_budget", 80)),
                    daily_hot_budget=int(getattr(v2ex_cfg, "daily_hot_budget", 40)),
                    daily_latest_budget=int(getattr(v2ex_cfg, "daily_latest_budget", 40)),
                    min_interval_minutes=int(getattr(v2ex_cfg, "min_interval_minutes", 5)),
                    detail_fetch_limit=int(getattr(v2ex_cfg, "detail_fetch_limit", 15)),
                    reply_enrichment_limit=int(getattr(v2ex_cfg, "reply_enrichment_limit", 10)),
                    max_topic_chars=int(getattr(v2ex_cfg, "max_topic_chars", 6000)),
                    max_reply_digest_chars=int(getattr(v2ex_cfg, "max_reply_digest_chars", 1200)),
                    max_profile_nodes=int(getattr(v2ex_cfg, "max_profile_nodes", 12)),
                    candidate_pipeline=new_candidate_pipeline,
                    keyword_fetch=new_keyword_fetch,
                    search_provider=build_v2ex_external_search_provider(new_config),
                )
            if new_weibo_client is not None:
                from openbiliclaw.runtime.weibo_producer import WeiboDiscoveryProducer

                new_weibo_producer = WeiboDiscoveryProducer(
                    database=self.database,
                    soul_engine=new_soul_engine,
                    client=new_weibo_client,
                    enabled=bool(getattr(weibo_cfg, "enabled", False))
                    and bool(getattr(sched_cfg, "enabled", True)),
                    source_modes=tuple(
                        getattr(weibo_cfg, "source_modes", ("search", "hot", "creator"))
                    ),
                    daily_search_budget=int(getattr(weibo_cfg, "daily_search_budget", 60)),
                    daily_hot_budget=int(getattr(weibo_cfg, "daily_hot_budget", 10)),
                    daily_creator_budget=int(getattr(weibo_cfg, "daily_creator_budget", 30)),
                    min_interval_minutes=int(getattr(weibo_cfg, "min_interval_minutes", 10)),
                    candidate_pipeline=new_candidate_pipeline,
                    keyword_fetch=new_keyword_fetch,
                )

        # P1.6: unified keyword planner — deficit-pulled merged keyword
        # generation. Built as its OWN object (the controller has no
        # llm_service field) holding llm_service + database + config, then
        # passed to the controller, which launches its loop in run_forever and
        # injects its own deficit / catalyst口径. Flag-off (default) → the loop
        # no-ops → zero behavior change.
        inspiration_provider = None
        if bool(getattr(discovery_cfg, "inspiration_search_enabled", False)):
            from openbiliclaw.config import derive_inspiration_breadth_params
            from openbiliclaw.discovery.inspiration_provider import (
                build_inspiration_search_provider,
                build_platform_source_backends,
            )

            inspiration_params = derive_inspiration_breadth_params(
                getattr(discovery_cfg, "inspiration_breadth", "medium")
            )
            inspiration_provider = build_inspiration_search_provider(
                getattr(discovery_cfg, "inspiration_search_backends", None),
                database=self.database,
                exa_api_key=str(getattr(discovery_cfg, "exa_api_key", "") or ""),
                you_api_key=str(getattr(discovery_cfg, "you_api_key", "") or ""),
                serply_api_key=str(getattr(discovery_cfg, "serply_api_key", "") or ""),
                platform_backends=build_platform_source_backends(
                    new_config,
                    database=self.database,
                    bilibili_client=new_bilibili_client,
                    x_client=new_x_client,
                    bangumi_client=new_bangumi_client,
                    github_client=new_github_client,
                    v2ex_client=new_v2ex_client,
                    weibo_client=new_weibo_client,
                ),
                platforms_per_probe=int(inspiration_params.platforms_per_probe),
                riskcontrolled_probe_budget=int(inspiration_params.riskcontrolled_probe_budget),
                pages_per_probe=int(inspiration_params.search_pages_per_probe),
            )
        from openbiliclaw.runtime.keyword_planner import KeywordPlanner

        new_keyword_planner = KeywordPlanner(
            llm_service=new_llm_service,
            database=self.database,
            config=new_config,
            soul_engine=new_soul_engine,
            pool_target_count=new_config.scheduler.pool_target_count,
            signal_event_threshold=int(getattr(new_config.scheduler, "signal_event_threshold", 6)),
            embedding_service=new_embedding_service,
            inspiration_provider=inspiration_provider,
        )

        async def _kick_source_incremental(source: str) -> None:
            publish = getattr(self.event_hub, "publish", None)
            if callable(publish):
                await publish({"type": f"{source}_task_available"})

        source_configs = getattr(new_config, "sources", None)

        def _source_enabled(name: str) -> bool:
            return bool(getattr(getattr(source_configs, name, None), "enabled", False))

        new_source_incremental_sync = SourceIncrementalSync(
            database=self.database,
            memory_manager=self.memory_manager,
            presence=self.presence,
            source_enabled={
                "xhs": _source_enabled("xiaohongshu"),
                "dy": _source_enabled("douyin"),
                "yt": _source_enabled("youtube"),
                "zhihu": _source_enabled("zhihu"),
                "reddit": _source_enabled("reddit"),
                "linuxdo": _source_enabled("linuxdo"),
                "v2ex": _source_enabled("v2ex"),
            },
            scheduler_config=new_config.scheduler,
            profile_ready=lambda: bool(new_soul_engine.is_profile_ready()),
            init_active=lambda: bool(self.init_coordinator.init_active()),
            runtime_config=new_config,
            kick=_kick_source_incremental,
        )

        new_runtime_controller = ContinuousRefreshController(
            memory_manager=self.memory_manager,
            database=self.database,
            soul_engine=new_soul_engine,
            discovery_engine=new_discovery_engine,
            recommendation_engine=new_recommendation_engine,
            discovery_candidate_pipeline=new_candidate_pipeline,
            keyword_planner=new_keyword_planner,
            keyword_fetch=new_keyword_fetch,
            source_incremental_sync=new_source_incremental_sync,
            pool_target_count=new_config.scheduler.pool_target_count,
            pool_source_shares=_pool_source_shares_from_config(new_config),
            signal_event_threshold=int(getattr(new_config.scheduler, "signal_event_threshold", 6)),
            trending_refresh_minutes=int(
                getattr(new_config.scheduler, "trending_refresh_minutes", 3)
            ),
            explore_refresh_minutes=int(
                getattr(new_config.scheduler, "explore_refresh_minutes", 3)
            ),
            check_interval_seconds=int(
                getattr(new_config.scheduler, "refresh_check_interval_seconds", 60)
            ),
            proactive_push_interval_seconds=int(
                getattr(new_config.scheduler, "proactive_push_interval_seconds", 120)
            ),
            discovery_limit=int(getattr(new_config.scheduler, "discovery_limit", 30)),
            event_hub=self.event_hub,
            bilibili_producer=new_bilibili_producer,
            xhs_producer=new_xhs_producer,
            douyin_producer=new_douyin_producer,
            youtube_producer=new_youtube_producer,
            x_producer=new_x_producer,
            zhihu_producer=new_zhihu_producer,
            reddit_producer=new_reddit_producer,
            bangumi_producer=new_bangumi_producer,
            github_producer=new_github_producer,
            linuxdo_producer=new_linuxdo_producer,
            v2ex_producer=new_v2ex_producer,
            weibo_producer=new_weibo_producer,
            scheduler_config=new_config.scheduler,
            presence=self.presence,
            # gui-init D1: pause the controller's background loops while a guided
            # init is active (account_sync already gates on the same predicate).
            # init's own run_init_backfill bypasses _llm_work_allowed.
            init_active_check=lambda: self.init_coordinator.init_active(),
            task_registry=self.task_registry,
            llm_concurrency_gate=new_llm_gate,
        )

        from openbiliclaw.runtime.candidate_eval import (
            CandidateEvalCoordinator,
            CandidateEvalSnapshot,
            effective_candidate_eval_workers,
        )

        def _candidate_eval_snapshot() -> CandidateEvalSnapshot:
            readiness = new_runtime_controller._pool_readiness_counts()  # noqa: SLF001
            new_runtime_controller._update_llm_inventory_state(  # noqa: SLF001
                int(readiness.get("available", 0))
            )
            status_counts = self.database.count_discovery_candidates_by_status()
            return CandidateEvalSnapshot(
                available=int(readiness.get("available", 0)),
                target=int(new_config.scheduler.pool_target_count),
                pending_eval=int(
                    status_counts.get(
                        "pending_eval_ready",
                        status_counts.get("pending_eval", 0),
                    )
                ),
                evaluating=int(status_counts.get("evaluating", 0)),
                evaluated_pending_admission=int(readiness.get("evaluated_pending", 0)),
                admitted_pending_copy=int(readiness.get("admitted_pending_copy", 0)),
                admitted_pending_available=int(readiness.get("admitted_pending_available", 0)),
                evaluated_waiting_total=int(status_counts.get("evaluated", 0)),
            )

        async def _request_candidate_supply(reason: str) -> dict[str, object]:
            return await new_runtime_controller.supply_candidates_once(reason=reason)

        async def _precompute_committed_candidates() -> None:
            expression_coordinator.notify("candidate_commit")

        from openbiliclaw.runtime.expression_copy import ExpressionCopyCoordinator

        async def _drain_expression_copy(limit: int) -> int:
            profile = await new_soul_engine.get_profile()
            if profile is None:
                return 0
            before = int(_candidate_eval_snapshot().available)
            completed = await new_recommendation_engine.drain_pending_expression_copy(
                profile=cast("Any", profile), limit=limit
            )
            await new_runtime_controller._publish_precompute_replenishment_if_needed(  # noqa: SLF001
                before_pool_count=before
            )
            return int(completed)

        expression_coordinator = ExpressionCopyCoordinator(
            pending_count_provider=new_recommendation_engine.count_pending_expression_copy_demand,
            drain_callback=_drain_expression_copy,
            safety_wake_seconds=float(
                getattr(new_config.scheduler, "refresh_check_interval_seconds", 60)
            ),
            work_allowed=lambda: new_runtime_controller._llm_work_allowed(),  # noqa: SLF001
        )
        new_runtime_controller.expression_copy_coordinator = expression_coordinator
        set_copy_callback = getattr(new_recommendation_engine, "set_copy_pending_callback", None)
        if callable(set_copy_callback):
            set_copy_callback(expression_coordinator.notify)

        candidate_eval_workers = effective_candidate_eval_workers(
            int(getattr(discovery_cfg, "candidate_eval_concurrency", 3)),
            llm_concurrency,
        )
        new_candidate_eval_coordinator = CandidateEvalCoordinator(
            pipeline=new_candidate_pipeline,
            snapshot_provider=_candidate_eval_snapshot,
            profile_provider=cast("Any", getattr(new_soul_engine, "get_profile", lambda: None)),
            worker_count=candidate_eval_workers,
            batch_size=30,
            supply_callback=_request_candidate_supply,
            post_commit_callback=_precompute_committed_candidates,
            on_admitted=lambda count: expression_coordinator.notify(f"candidate_admitted:{count}"),
            work_allowed=lambda: (
                new_runtime_controller._is_initialized()  # noqa: SLF001
                and new_runtime_controller._llm_work_allowed()  # noqa: SLF001
            ),
            # Pool-share fairness (spec 2026-07-20, D7): run the controller's
            # share rebalance + deficit summary each coordinator tick before
            # admission. The coordinator assembly replaces _loop_candidate_eval,
            # so without this the Phase 3/4 hooks are dead code in production.
            # Guarded for controllers/test doubles lacking the helper.
            pre_admit_hook=getattr(new_runtime_controller, "run_pool_share_maintenance", None),
            safety_wake_seconds=float(
                getattr(new_config.scheduler, "refresh_check_interval_seconds", 60)
            ),
        )
        new_runtime_controller.candidate_eval_coordinator = new_candidate_eval_coordinator
        new_candidate_pipeline.on_candidates_enqueued = lambda _count: (
            new_candidate_eval_coordinator.notify("candidate_enqueued:pipeline")
        )
        # Pool-share fairness (spec 2026-07-20, Phase 2): let admission see the
        # per-family visible-pool targets so under-share sources win freed slots
        # ahead of an over-supplied source's backlog. Bound after controller
        # construction so the pipeline reuses the controller's canonical
        # ``_source_target_counts`` (family-keyed) share口径. Guarded so test
        # doubles / alternate controllers without the helper keep legacy (None)
        # admission instead of raising at bootstrap.
        _source_target_counts = getattr(new_runtime_controller, "_source_target_counts", None)
        if callable(_source_target_counts):
            new_candidate_pipeline.source_share_targets = _source_target_counts
        for producer in (
            new_douyin_producer,
            new_youtube_producer,
            new_zhihu_producer,
            new_bangumi_producer,
            new_github_producer,
            new_linuxdo_producer,
            new_v2ex_producer,
            new_weibo_producer,
        ):
            if producer is not None:
                producer.candidate_evaluation_owned_by_coordinator = True
        set_pool_commit_callback = getattr(
            new_recommendation_engine,
            "set_pool_inventory_commit_callback",
            None,
        )
        if callable(set_pool_commit_callback):
            set_pool_commit_callback(self.pool_inventory_commit_callback)

        # 9. Account sync
        account_sync_x_client, account_sync_x_health = _build_account_sync_x_components(
            new_config,
            self.database,
        )
        new_account_sync = AccountSyncService(
            memory_manager=self.memory_manager,
            bilibili_client=new_bilibili_client,
            soul_engine=new_soul_engine,
            sync_interval_hours=new_config.scheduler.account_sync_interval_hours,
            llm_work_allowed=self.background_llm_work_allowed,
            database=self.database,
            x_client=account_sync_x_client,
            # A separate store instance shares the producer's one-row DB state
            # but is fingerprint-bound to this exact client's cookie. That keeps
            # cooldown global without crediting a success to a different cookie
            # if configuration changes during a rebuild.
            x_health_store=account_sync_x_health,
        )

        # 10. Dialogue (with source management tools) + one settlement queue
        from openbiliclaw.soul.dialogue import DialogueLearningMode
        from openbiliclaw.soul.dialogue_learn_queue import DialogueSettlementQueue
        from openbiliclaw.sources.tools import SOURCE_TOOLS, SourceToolDispatcher

        source_tool_dispatcher = SourceToolDispatcher(self.database)
        anchor_manager = getattr(new_soul_engine, "_dialogue_anchor_manager", None)
        anchor_provider = getattr(anchor_manager, "snapshot", None)
        new_settlement_queue = DialogueSettlementQueue(
            _build_dialogue_settlement_dispatcher(
                new_soul_engine,
                self.dialogue_settlement_handlers,
            ),
            anchor_provider=anchor_provider if callable(anchor_provider) else None,
            guard=self.dialogue_settlement_guard,
        )
        bind_settlement_queue = getattr(
            new_soul_engine,
            "bind_dialogue_settlement_queue",
            None,
        )
        if callable(bind_settlement_queue):
            bind_settlement_queue(new_settlement_queue)
        new_dialogue = SocraticDialogue(
            llm=None,
            soul_engine=new_soul_engine,
            llm_service=new_llm_service,
            session="popup",
            tools=SOURCE_TOOLS,
            tool_dispatcher=source_tool_dispatcher,
            database=self.database,
            learning_mode=DialogueLearningMode.QUEUED,
            settlement_queue=new_settlement_queue,
        )

        # 11. Auto-update service
        try:
            new_auto_update = AutoUpdateService(
                enabled=new_config.scheduler.auto_update_enabled,
                check_interval_hours=new_config.scheduler.auto_update_check_interval_hours,
                allow_prerelease=new_config.scheduler.auto_update_allow_prerelease,
                allowed_remotes=new_config.scheduler.auto_update_allowed_remotes,
                event_publisher=getattr(self.event_hub, "publish", None),
            )
        except Exception:
            new_auto_update = AutoUpdateService(
                enabled=False,
                event_publisher=getattr(self.event_hub, "publish", None),
            )

        # Carry the last update-check result forward so a config save (which
        # rebuilds this service) doesn't reset the settings page from "发现新版本"
        # back to "尚未检查更新" until the next scheduled check.
        old_auto_update = getattr(self, "auto_update_service", None)
        if old_auto_update is not None:
            with suppress(Exception):
                new_auto_update.adopt_status_from(old_auto_update)

        # ── Atomic swap ─────────────────────────────────────────────
        # All construction succeeded. In an active loop, start() creates the
        # actual Task and synchronously registers its permit before any new
        # runtime attribute is published.
        new_settlement_queue.start()
        new_llm_gate.reconfigure(llm_concurrency)
        self.llm_concurrency_gate = new_llm_gate
        self.config = new_config
        self.llm_registry = new_registry
        self.llm_service = new_llm_service
        self.bilibili_client = new_bilibili_client
        old_bangumi_client = self.bangumi_client
        old_github_client = self.github_client
        old_v2ex_client = self.v2ex_client
        old_weibo_client = self.weibo_client
        self.bangumi_client = new_bangumi_client
        self.github_client = new_github_client
        self.v2ex_client = new_v2ex_client
        self.weibo_client = new_weibo_client
        self.saved_sync_service = new_saved_sync_service
        self.soul_engine = new_soul_engine
        self.dialogue = new_dialogue
        self.dialogue_settlement_queue = new_settlement_queue
        self.discovery_engine = new_discovery_engine
        self.recommendation_engine = new_recommendation_engine
        self.runtime_controller = new_runtime_controller
        self.account_sync_service = new_account_sync
        self.auto_update_service = new_auto_update
        if old_bangumi_client is not None and old_bangumi_client is not new_bangumi_client:
            close = getattr(old_bangumi_client, "aclose", None)
            if callable(close):
                with suppress(RuntimeError):
                    self.task_registry.track("close_old_bangumi_client", close())
        if old_github_client is not None and old_github_client is not new_github_client:
            close = getattr(old_github_client, "aclose", None)
            if callable(close):
                with suppress(RuntimeError):
                    self.task_registry.track("close_old_github_client", close())
        if old_v2ex_client is not None and old_v2ex_client is not new_v2ex_client:
            close = getattr(old_v2ex_client, "aclose", None)
            if callable(close):
                with suppress(RuntimeError):
                    self.task_registry.track("close_old_v2ex_client", close())
        if old_weibo_client is not None and old_weibo_client is not new_weibo_client:
            close = getattr(old_weibo_client, "aclose", None)
            if callable(close):
                with suppress(RuntimeError):
                    self.task_registry.track("close_old_weibo_client", close())
        if new_inventory_available is not None:
            new_llm_gate.update_inventory(
                available=new_inventory_available,
                target=int(new_config.scheduler.pool_target_count),
            )
        # Drop the cached init prerequisite probes (chat/bilibili) — config or
        # cookie just changed, so the next /api/init pre-flight must re-probe
        # against the new provider/cookie instead of a stale TTL value (gui-init
        # review). The InitCoordinator is intentionally NOT reset: it holds the
        # current run handle and reads ctx components lazily, so it survives a
        # rebuild (rebuild also excludes the guided_init task from cancellation).
        self._init_prereqs = None

        logger.info(
            "Hot-reload complete — rebuilt %d swappable components",
            12,
        )

    async def restart_background_tasks(
        self,
        app: FastAPI,
        *,
        run_post_reload_llm_work: bool = True,
    ) -> None:
        """Cancel old background tasks and start new ones from current components."""
        # Cancel existing tasks. A third-party/provider coroutine may swallow
        # cancellation; never let a config hot-reload (which precedes guided
        # init reservation) wait forever and look like a dead POST /api/init.
        stuck_tasks: set[str] = set()
        current_loop = asyncio.get_running_loop()
        for attr in ("refresh_task", "account_sync_task", "auto_update_task"):
            task = getattr(app.state, attr, None)
            if task is not None:
                # TestClient and embedded hosts may reuse RuntimeContext across
                # event-loop lifetimes. A pending Task owned by a closed/foreign
                # loop cannot be awaited or safely cancelled from this loop;
                # retain ownership and, crucially, do not start a duplicate.
                task_loop = task.get_loop()
                if task_loop is not current_loop:
                    if task.done():
                        try:
                            task.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            logger.warning(
                                "Stale %s from a prior event loop exited with an error",
                                attr,
                                exc_info=True,
                            )
                    else:
                        stuck_tasks.add(attr)
                        logger.warning(
                            "Cannot cancel stale %s owned by a different event loop; "
                            "leaving it attached and suppressing duplicate startup",
                            attr,
                        )
                    continue
                task.cancel()
                done, _ = await asyncio.wait(
                    {task}, timeout=_BACKGROUND_TASK_CANCEL_TIMEOUT_SECONDS
                )
                if task not in done:
                    stuck_tasks.add(attr)
                    logger.warning("Timed out cancelling stale %s during hot-reload", attr)
                    continue
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.warning(
                        "Stale %s exited with an error during hot-reload", attr, exc_info=True
                    )

        # Start new tasks from the freshly-built components.
        # v0.3.63+: route through ``self.task_registry.track`` so the
        # next hot-reload's ``cancel_all`` cleanly stops them too.
        # ``False`` is also the setup/guided-init lane fence: starting the
        # controller here would start every discovery loop, not only the
        # extension-account scheduler. The normal post-init restart owns the
        # one replacement controller and its independent source loop.
        if run_post_reload_llm_work:
            run_forever = getattr(self.runtime_controller, "run_forever", None)
            if "refresh_task" not in stuck_tasks:
                app.state.refresh_task = (
                    self.task_registry.track("refresh_loop", run_forever())
                    if callable(run_forever)
                    else None
                )

            sync_forever = getattr(self.account_sync_service, "run_forever", None)
            if "account_sync_task" not in stuck_tasks:
                app.state.account_sync_task = (
                    self.task_registry.track("account_sync_loop", sync_forever())
                    if callable(sync_forever)
                    else None
                )
        else:
            if "refresh_task" not in stuck_tasks:
                app.state.refresh_task = None
            if "account_sync_task" not in stuck_tasks:
                app.state.account_sync_task = None

        update_forever = getattr(self.auto_update_service, "run_forever", None)
        if "auto_update_task" not in stuck_tasks:
            app.state.auto_update_task = (
                self.task_registry.track("auto_update_loop", update_forever())
                if callable(update_forever)
                else None
            )

        llm_work_allowed = run_post_reload_llm_work and self.background_llm_work_allowed()

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
                            "probe_feedback_history",
                            self.memory_manager,
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
                            "avoidance_probe_feedback_history",
                            self.memory_manager,
                        ),
                    )
                    logger.debug("post-reload avoidance speculator scheduled as background task")

                # v0.3.124+ (lever 2a): the cancel_all in rebuild_from_config
                # also killed any in-flight classify_pool_backlog /
                # precompute_pool_copy / delight scoring. Without a re-kick a
                # user saving config mid-cold-start strands pool-fill until
                # the next 60s refresh tick — or indefinitely if they keep
                # saving. Re-kick the classify→copy→delight drain on the
                # freshly-built engine so pool-fill resumes immediately.
                # precompute_pool_copy spawns classify + delight detached
                # internally, so one call restarts the whole trio.
                classify = getattr(self.recommendation_engine, "classify_pool_backlog", None)
                delight = getattr(self.recommendation_engine, "precompute_delight_scores", None)
                if callable(classify):
                    self.task_registry.track(
                        "post_reload_classify_pool_backlog",
                        classify(profile=profile, limit=60),
                    )
                if callable(delight):
                    self.task_registry.track(
                        "post_reload_precompute_delight_scores",
                        delight(profile=profile, limit=30),
                    )
                coordinator = getattr(self.runtime_controller, "expression_copy_coordinator", None)
                if coordinator is not None:
                    coordinator.notify("hot_reload")
                else:
                    precompute = getattr(self.recommendation_engine, "precompute_pool_copy", None)
                    if callable(precompute):
                        self.task_registry.track(
                            "post_reload_precompute_pool_copy",
                            self._safe_post_reload_precompute(precompute, profile),
                        )
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

        if run_post_reload_llm_work:
            logger.info("Background tasks restarted after hot-reload")
        else:
            logger.info("Background LLM tasks suspended after setup config hot-reload")

    @staticmethod
    async def _safe_post_reload_speculate(
        speculator: Any,
        profile: Any,
        feedback_history: object,
        feedback_history_key: str,
        memory_manager: Any,
    ) -> None:
        """Run post-reload speculation without blocking config PUT."""
        load_runtime_state = getattr(memory_manager, "load_discovery_runtime_state", None)

        def _load_feedback_history() -> object:
            if not callable(load_runtime_state):
                return []
            runtime_state = load_runtime_state()
            if not isinstance(runtime_state, dict):
                return []
            return runtime_state.get(feedback_history_key, [])

        try:
            try:
                await speculator.force_tick(
                    profile,
                    feedback_history=feedback_history,
                    feedback_history_loader=_load_feedback_history,
                )
            except TypeError:
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
    async def _safe_post_reload_precompute(precompute_callable: Any, profile: Any) -> None:
        """Re-kick the classify→copy→delight drain after a hot-reload.

        ``rebuild_from_config``'s ``cancel_all`` stops any in-flight
        classify_pool_backlog / precompute_pool_copy / delight scoring (they
        hold references to the now-swapped-out engine). One
        ``precompute_pool_copy`` call restarts the whole trio on the fresh
        engine — its own ``_expression_lock`` keeps it from racing the
        refresh loop's periodic drain, which remains the backstop. Failures
        are logged, not fatal to the config PUT.
        """
        try:
            await precompute_callable(profile=profile)
        except Exception:
            logger.exception("post-reload precompute_pool_copy failed")

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

        v0.3.124+ (lever 4): the retry loop only makes sense when there
        is something to warm but it failed (backend warming up / down).
        ``prewarm`` now returns ``-1`` when there is simply nothing to
        warm yet (empty pool / no embedding service) — a benign cold
        start, not a failure — so we log it plainly and stop instead of
        burning 5 alarming "warmed=0 — retry" lines on every fresh deploy
        (which read identically to a real Ollama outage). ``0`` with
        candidates present is the genuine "backend unreachable" case and
        keeps the retry-then-warn behaviour.
        """
        delay = 2.0
        for attempt in range(1, 6):
            try:
                warmed = await prewarm_callable()
                if isinstance(warmed, int):
                    if warmed > 0:
                        return
                    if warmed < 0:
                        # Nothing to warm yet — benign cold start; retrying
                        # won't help (the cache lazy-fills as the pool fills).
                        logger.info(
                            "Startup prewarm_pool_mmr_embeddings: nothing to warm yet "
                            "(empty pool or embedding service off) — skipping retries; "
                            "cache will lazy-fill from serve()/discovery traffic"
                        )
                        return
                logger.info(
                    "Startup prewarm_pool_mmr_embeddings attempt %d embedded 0 items "
                    "(candidates present — embedding backend may be warming up/down) "
                    "— retry in %.1fs",
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
        logger.warning(
            "Startup prewarm_pool_mmr_embeddings gave up after retries — the embedding "
            "backend stayed unreachable (candidates were present but none embedded; "
            "e.g. Ollama down). MMR diversity degrades; cache will lazy-fill if it recovers"
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
    from openbiliclaw.runtime.updater import AutoUpdateService
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

    # Keep update check / apply available in degraded mode — a backend that
    # can't build its LLM registry is exactly when the user may want to pull a
    # fix-carrying release. Construction is cheap and network-free; never let it
    # break the degraded recovery context.
    degraded_auto_update: AutoUpdateService | None = None
    with suppress(Exception):
        degraded_auto_update = AutoUpdateService(
            enabled=config.scheduler.auto_update_enabled,
            check_interval_hours=config.scheduler.auto_update_check_interval_hours,
            allow_prerelease=config.scheduler.auto_update_allow_prerelease,
            allowed_remotes=config.scheduler.auto_update_allowed_remotes,
            event_publisher=getattr(event_hub, "publish", None),
        )

    message = str(exc) if exc is not None else "LLM registry unavailable"
    return RuntimeContext(
        database=database,
        memory_manager=memory_manager,
        event_hub=event_hub,
        config=config,
        auto_update_service=degraded_auto_update,
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
