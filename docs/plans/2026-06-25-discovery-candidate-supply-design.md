# Discovery Candidate Supply Design

## Goal

Keep the Evo evaluator fed with real, unseen `pending_eval` candidates. Evo remains a consumer of the database queue; Discover becomes responsible for filling that queue to a useful waterline before evaluation runs.

## Current Problem

Runtime refresh fetches raw candidates once, inserts what it can, then drains Evo. Historical duplicates are removed by `INSERT OR IGNORE`, so a raw batch of 30 can become only 1-3 new `pending_eval` rows. Evo then evaluates the small queue it has; it does not fetch more content.

## Design

Add a supply fill loop to `DiscoveryCandidatePipeline` and call it from refresh when available.

- Count active supply as `pending_eval + evaluating`.
- If active supply is below the target batch size, produce raw candidates and enqueue only genuinely new rows.
- After each attempt, check the actual inserted count and the active supply count.
- Continue until the target is reached, the pool becomes full, attempts are exhausted, or the time budget expires.
- Keep this loop under the existing refresh lock. Do not introduce another Discover worker.
- Keep Evo drain under the existing drain locks.

Before enqueue, filter known content:

- Existing rows in `discovery_candidates`, regardless of lifecycle status.
- Existing Bili `content_cache` rows by BVID/content id.
- Duplicates within the current enqueue call.

Related-chain seed selection should prefer explicit positive signals:

- `favorite`, `like`, `coin`, `share`, and positive feedback first.
- `view` only as fallback, or when it has positive satisfaction.

## Concurrency

The existing `ContinuousRefreshController._refresh_lock` remains the only Discover serialization point. Periodic refresh, manual refresh, event-triggered replenishment, and init replenishment cannot run multiple supply loops at once. Evo can consume in parallel, but the supply loop counts `evaluating` rows so it does not overproduce while an Evo batch is in flight.

## Diagnostics

Log each supply fill pass with:

- target supply, pending, evaluating, and attempt
- raw produced count
- known-filtered count
- inserted count
- final pending/evaluating counts
- stop reason

