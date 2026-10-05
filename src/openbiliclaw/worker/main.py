"""Standalone background maintenance and full worker process.

This package is the API/worker isolation worker entry point:

- In maintenance mode it owns a separate ``Database`` and runs pool
  maintenance plus the serve snapshot/outbox bridge.
- In full mode (``OPENBILICLAW_FULL_WORKER=1``) it builds the same
  ``RuntimeContext`` as the API process and owns all periodic background
  loops (refresh, account sync, auto-update, discovery producers, candidate
  evaluation, soul pipeline, precompute, etc.).  The API process skips its
  own background loops when the full worker is active.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

import asyncio
import contextlib
import logging
import os
import time

from openbiliclaw.config import load_config
from openbiliclaw.runtime.serve_outbox import ServeOutbox
from openbiliclaw.runtime.serve_snapshot import ServeSnapshotStore
from openbiliclaw.runtime.worker_status import WorkerStatusStore
from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_MAX_BATCHES_PER_TICK = 10
DEFAULT_MAX_MUTATIONS_PER_BATCH = 50
DEFAULT_SNAPSHOT_CANDIDATE_LIMIT = 400
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 15.0
DEFAULT_SERVE_PUBLISH_INTERVAL_SECONDS = 20.0
# Retry cadence while the on-disk config cannot build any LLM instance yet.
# The probe only constructs the registry (no network) and re-reads
# config.toml on every attempt, so saving a usable key is picked up in-place.
DEFAULT_LLM_PROBE_RETRY_SECONDS = 15.0


async def _drain_serve_outbox(database: Database, runtime_dir: Path) -> None:
    outbox = ServeOutbox(runtime_dir / "serve_outbox.jsonl")
    try:
        batches = await asyncio.to_thread(outbox.claim_batches)
        for batch, records in batches:
            for record in records:
                rows = list(record.get("recommendation_rows") or [])
                bvids = list(record.get("ranked_bvids") or [])
                await database.persist_pool_serve_async(rows, bvids)
            await asyncio.to_thread(outbox.acknowledge, batch)
        if batches:
            logger.info("drained %d serve outbox batch(es)", len(batches))
    except Exception:
        logger.exception("Background serve outbox drain failed")


async def _publish_serve_snapshot(
    database: Database,
    runtime_dir: Path,
    *,
    snapshot_limit: int = DEFAULT_SNAPSHOT_CANDIDATE_LIMIT,
    max_age_seconds: float = DEFAULT_INTERVAL_SECONDS * 2,
) -> None:
    snapshot_store = ServeSnapshotStore(
        runtime_dir / "serve_snapshot.json",
        max_age_seconds=max_age_seconds,
    )
    try:
        snapshot = await database.load_pool_serve_snapshot_async(
            limit=snapshot_limit,
            curator_history_limit=30,
        )
        snapshot_store.save(snapshot)
        logger.info(
            "worker published serve snapshot loaded=%s available=%s raw=%s",
            getattr(snapshot, "loaded_count", "?"),
            getattr(snapshot, "readiness", {}).get("available", "?"),
            getattr(snapshot, "readiness", {}).get("raw", "?"),
        )
    except Exception:
        logger.exception("Background serve snapshot publish failed")


async def _run_serve_publisher_loop(
    database: Database,
    runtime_dir: Path,
    *,
    interval_seconds: float = DEFAULT_SERVE_PUBLISH_INTERVAL_SECONDS,
    snapshot_limit: int = DEFAULT_SNAPSHOT_CANDIDATE_LIMIT,
) -> None:
    """Drain API serve outbox writes and publish a fresh snapshot periodically.

    The full worker's RuntimeContext already performs pool maintenance and the
    heavy background loops.  This separate lightweight loop preserves the Phase
    1/2 contract: API recommend responses read the atomically-published snapshot
    and the API never waits on SQLite writes.
    """
    while True:
        await _drain_serve_outbox(database, runtime_dir)
        await _publish_serve_snapshot(
            database,
            runtime_dir,
            snapshot_limit=snapshot_limit,
            max_age_seconds=max(interval_seconds * 2, 30.0),
        )
        await asyncio.sleep(max(0.0, float(interval_seconds)))


async def _run_worker_heartbeat_loop(
    status_store: WorkerStatusStore,
    *,
    mode: str,
    interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """Publish a lightweight worker heartbeat for /api/runtime-status."""
    started_at = time.time()
    while True:
        try:
            status_store.write(
                mode=mode,
                pid=os.getpid(),
                started_at=started_at,
                heartbeat_at=time.time(),
            )
        except Exception:
            logger.exception("Worker heartbeat publish failed")
        await asyncio.sleep(max(1.0, float(interval_seconds)))


async def run_maintenance_worker(
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    max_batches_per_tick: int = DEFAULT_MAX_BATCHES_PER_TICK,
    max_mutations_per_batch: int = DEFAULT_MAX_MUTATIONS_PER_BATCH,
) -> None:
    """Run pool maintenance and publish a serve snapshot in a separate process.

    This is deliberately independent from the API runtime: it uses its own
    database handle and never imports the FastAPI server or the full
    ``RuntimeContext``. The API process can later stop scheduling the same
    maintenance and delegate it here.
    """
    config = load_config()
    runtime_dir = config.data_path / "runtime"
    database = Database(config.data_path / "openbiliclaw.db")
    database.initialize()
    status_store = WorkerStatusStore(
        runtime_dir / "worker_status.json",
        max_age_seconds=max(interval_seconds * 2, 30.0),
    )
    heartbeat_task = asyncio.create_task(
        _run_worker_heartbeat_loop(status_store, mode="maintenance"),
        name="worker_heartbeat",
    )
    try:
        target = int(getattr(config.scheduler, "pool_target_count", 300) or 300)
        raw_ceiling = max(target * 2, target + 120)
        while True:
            worked = False
            for _batch in range(max(1, int(max_batches_per_tick))):
                try:
                    result = await database.maintain_pool_inventory_async(
                        target=target,
                        raw_ceiling=raw_ceiling,
                        source_share_quotas={},
                        raw_source_share_quotas={},
                        max_per_topic_group=max(3, target // 10),
                        max_per_explore_cluster=3,
                        max_mutations=max(1, int(max_mutations_per_batch)),
                    )
                except Exception:
                    logger.exception("Background pool maintenance failed")
                    break
                worked = True
                logger.info(
                    "worker pool_maintenance available=%s->%s has_more=%s total_ms=%.1f",
                    getattr(result, "available_before", "?"),
                    getattr(result, "available_after", "?"),
                    bool(getattr(result, "has_more", False)),
                    float(getattr(result, "total_ms", 0.0)),
                )
                if not getattr(result, "has_more", False):
                    break
                await asyncio.sleep(0)

            await _drain_serve_outbox(database, runtime_dir)
            await _publish_serve_snapshot(
                database,
                runtime_dir,
                snapshot_limit=DEFAULT_SNAPSHOT_CANDIDATE_LIMIT,
                max_age_seconds=interval_seconds * 2,
            )
            if not worked:
                await asyncio.sleep(min(5.0, max(0.0, float(interval_seconds))))
            else:
                await asyncio.sleep(max(0.0, float(interval_seconds)))
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        database.close()


async def _wait_for_llm_registry(
    *,
    retry_interval_seconds: float | None = None,
    probe: Callable[[], None] | None = None,
) -> None:
    """Wait until the on-disk config builds at least one usable LLM instance.

    A desktop/CLI launch whose config has no buildable LLM instance used to
    crash this worker before it ever served a heartbeat: windowed builds popped
    a PyInstaller error dialog, and after the user later saved a valid key the
    background loops stayed dead until restart. The probe only constructs the
    registry (no network calls) and re-reads ``config.toml`` on every attempt,
    so a key saved from the setup wizard is picked up without a restart.
    """
    from openbiliclaw.llm.registry import RegistryBuildError, build_llm_registry

    retry_interval = (
        DEFAULT_LLM_PROBE_RETRY_SECONDS
        if retry_interval_seconds is None
        else float(retry_interval_seconds)
    )
    probe_fn = probe
    if probe_fn is None:

        def _default_probe() -> None:
            build_llm_registry(load_config())

        probe_fn = _default_probe

    waiting_logged = False
    while True:
        try:
            await asyncio.to_thread(probe_fn)
            if waiting_logged:
                logger.info("LLM registry builds again; starting full worker")
            return
        except RegistryBuildError as exc:
            if not waiting_logged:
                logger.warning(
                    "Full worker is waiting for a buildable LLM configuration (%s); "
                    "retrying every %.0fs while keeping the heartbeat alive",
                    exc,
                    retry_interval,
                )
                waiting_logged = True
        except Exception:
            logger.exception(
                "Full worker LLM probe failed unexpectedly; retrying in %.0fs",
                retry_interval,
            )
        # Floor keeps a misconfigured zero interval from hot-spinning; the
        # production default is 15s (tests inject a tiny interval).
        await asyncio.sleep(max(0.05, retry_interval))


async def run_full_worker() -> None:
    """Run a full RuntimeContext background worker in a separate process.

    This builds the same runtime components as the API process and starts the
    periodic background loops (refresh/account sync/auto-update and related
    daemon work). When ``OPENBILICLAW_FULL_WORKER=1``, the API process should
    skip starting these loops so they run here instead.
    """
    from types import SimpleNamespace

    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.runtime.feedback_scheduler import EventProcessingScheduler

    config = load_config()
    runtime_dir = config.data_path / "runtime"
    status_store = WorkerStatusStore(
        runtime_dir / "worker_status.json",
        max_age_seconds=max(DEFAULT_HEARTBEAT_INTERVAL_SECONDS * 3, 30.0),
    )
    heartbeat_task = asyncio.create_task(
        _run_worker_heartbeat_loop(status_store, mode="full"),
        name="worker_heartbeat",
    )
    app = SimpleNamespace(state=SimpleNamespace())
    publisher_task: asyncio.Task[None] | None = None
    feedback_scheduler: EventProcessingScheduler | None = None
    ctx = None
    try:
        # Degraded boot: never let a missing/invalid LLM config kill the
        # process. Waiting with a live heartbeat lets the API report the
        # worker as alive (degraded) and lets the user fix config.toml in the
        # setup wizard without restarting the app.
        await _wait_for_llm_registry()
        config = load_config()
        ctx = build_runtime_context(config)
        await ctx.restart_background_tasks(app)
        logger.info("Full worker background tasks started")
        feedback_scheduler = EventProcessingScheduler(
            soul_engine_resolver=lambda: getattr(ctx, "soul_engine", None),
            debounce_seconds=5.0,
        )
        feedback_scheduler.start_periodic()
        database = getattr(ctx, "database", None)
        if database is not None:
            publisher_task = asyncio.create_task(
                _run_serve_publisher_loop(database, runtime_dir),
                name="worker_serve_publisher",
            )
        while True:
            await asyncio.sleep(3600)
    finally:
        if publisher_task is not None:
            publisher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await publisher_task
        try:
            task_registry = getattr(ctx, "task_registry", None) if ctx is not None else None
            if task_registry is not None:
                cancel_all = getattr(task_registry, "cancel_all", None)
                if callable(cancel_all):
                    await cancel_all()
        finally:
            if feedback_scheduler is not None:
                close = getattr(feedback_scheduler, "close", None)
                if callable(close):
                    await close()
            if ctx is not None:
                database = getattr(ctx, "database", None)
                close = getattr(database, "close", None)
                if callable(close):
                    close()
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        if os.environ.get("OPENBILICLAW_FULL_WORKER", "").strip() == "1":
            asyncio.run(run_full_worker())
        else:
            asyncio.run(run_maintenance_worker())
    except KeyboardInterrupt:
        logger.info("Background worker interrupted")


if __name__ == "__main__":
    main()
