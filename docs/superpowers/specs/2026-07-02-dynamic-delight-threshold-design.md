# Dynamic Delight Threshold Design

## Goal

惊喜推荐不再只依赖固定分数线。候选已经进入正式推荐池后，只有当前池内分数分布的 Top 10% 才应被当作主动惊喜推荐；初始化或样本不足时继续使用现有默认阈值。

## Scope

This change applies to the backend delight selection path only:

- formal candidate pool: `content_cache` rows that are currently servable by recommendation/delight paths
- delight scoring and copy backfill: `RecommendationEngine.precompute_delight_scores`
- pending delight queries/counts: runtime, API, CLI, and storage claim guard

It does not apply to raw `discovery_candidates` rows that have not passed evaluation/admission.

## Threshold Rule

Compute the effective delight threshold as:

```text
effective_threshold = max(profile_default_threshold, pool_score_p90)
```

Where:

- `profile_default_threshold` is the current profile-aware floor:
  - `0.70` normally
  - `0.80` when `exploration_openness < 0.3`
- `pool_score_p90` is the Top 10% boundary among the current formal candidate pool
- if the pool has too few usable scored rows, `pool_score_p90` is unavailable and the default floor is used

Using `max()` prevents a weak pool from lowering the quality bar just because an item is technically in the top 10%.

Implementation detail: sort usable pool scores descending, take `ceil(n * 0.10)` rows, and use the last selected row's score as the boundary. If `n < 20`, skip percentile calculation and use the profile-aware default. This makes "Top 10%" concrete and avoids one or two startup rows creating an artificial dynamic threshold.

## Data Source

The percentile should use the same score family delight already persists:

- primary score: `relevance_score`, because `precompute_delight_scores` maps Evo relevance directly into `delight_score`
- eligible rows: formal pool rows with statuses `fresh` and `shown`
- excluded rows: disliked feedback, raw discovery rows, and non-admitted candidates

The exact SQL should live in storage so all callers share one threshold source.

## Update Strategy

Prefer on-demand calculation over a scheduled threshold job.

Reasoning:

- the threshold is a pure function of current `content_cache` scores
- on-demand avoids stale threshold state after discovery/admission changes
- the existing delight paths already query storage before serving or counting
- a TTL cache can be added later if profiling shows the percentile query is expensive

## Behavior

When enough pool rows exist:

- delight copy is generated only for candidates whose score is at or above the dynamic threshold
- pending delight retrieval uses the same dynamic threshold
- pending delight counts and normal-feed exclusion use the same dynamic threshold to avoid duplicate or stranded content

When the pool is empty or too small:

- the system behaves like today, using `0.70` or `0.80` depending on exploration openness

## Testing

Add focused tests for:

- percentile threshold returns the Top 10% boundary when enough scored pool rows exist
- dynamic threshold never drops below the profile-aware default
- insufficient samples fall back to the default threshold
- runtime pending delight queries pass the dynamic threshold to storage
- storage feed-exclusion and delight-queue predicates stay in sync

## Documentation

Update:

- `docs/modules/recommendation.md`
- `docs/modules/storage.md`
- `docs/changelog.md`
- recommendation architecture diagram text if it mentions the fixed `0.70` delight threshold
