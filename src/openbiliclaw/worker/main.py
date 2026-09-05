"""Standalone background maintenance and snapshot worker.

Phase 0/1 of the API/worker isolation architecture:

- The worker owns its own ``Database`` and runs pool maintenance.
- It publishes an atomically replaced recommendation serve snapshot.
- It is intended to be launched as a separate process, e.g.
  ``python -m openbiliclaw.worker``.
- Later phases will expand this process to own discovery/eval,
  LLM/embedding, dialogue settlement, and the remaining API-side
  background work, while the API process becomes read/serve-only.
"""

from __future__ import annotations

import asyncio
import logging

from openbiliclaw.config import load_config
from openbiliclaw.runtime.serve_outbox import ServeOutbox
from openbiliclaw.runtime.serve_snapshot import ServeSnapshotStore
from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_MAX_BATCHES_PER_TICK = 10
DEFAULT_MAX_MUTATIONS_PER_BATCH = 50
DEFAULT_SNAPSHOT_CANDIDATE_LIMIT = 400


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
    database = Database(config.data_path / "openbiliclaw.db")
    database.initialize()
    snapshot_store = ServeSnapshotStore(
        config.data_path / "runtime" / "serve_snapshot.json",
        max_age_seconds=interval_seconds * 2,
    )
    outbox = ServeOutbox(config.data_path / "runtime" / "serve_outbox.jsonl")
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
            try:
                records = outbox.read_all()
                if records:
                    for record in records:
                        rows = list(record.get("recommendation_rows") or [])
                        bvids = list(record.get("ranked_bvids") or [])
                        await database.persist_pool_serve_async(rows, bvids)
                    outbox.clear()
                    logger.info("drained %d serve outbox batch(es)", len(records))
            except Exception:
                logger.exception("Background serve outbox drain failed")

            try:
                snapshot = database.load_pool_serve_snapshot(
                    limit=DEFAULT_SNAPSHOT_CANDIDATE_LIMIT,
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
            if not worked:
                await asyncio.sleep(min(5.0, max(0.0, float(interval_seconds))))
            else:
                await asyncio.sleep(max(0.0, float(interval_seconds)))
    finally:
        database.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_maintenance_worker())
    except KeyboardInterrupt:
        logger.info("Background worker interrupted")


if __name__ == "__main__":
    main()
