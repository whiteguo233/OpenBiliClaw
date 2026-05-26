# 2026-05-24 — Servable Pool Count Spec

## 0. Scope

This spec fixes one user-visible contract: every surface that says "还有 N 条可换"
must use the number of items the backend can actually serve right now.

Affected surfaces:

| Surface | Current symptom | Required outcome |
|---------|-----------------|------------------|
| Extension popup / side panel | Screenshot/log showed "还有 142 条可换" while "换一批" returned no cards | "当前可换" only shows immediately servable items |
| Mobile web `/m/` | Pool summary may reuse the same runtime status count | Same servable count and empty/pending copy as extension |
| Desktop web `/web` | Runtime/pool widgets may show pool inventory as availability | Same servable count and pending/material distinction |

Out of scope:

- Changing recommendation ranking.
- Serving unclassified or copy-less content.
- Solving Gemini quota/network failures directly. Those failures explain why rows stay pending, but this spec fixes the incorrect availability signal.

## 1. Problem

Logs showed the exact mismatch:

```text
serve(/pool) loaded 0 candidates from pool of 142
Recommendation candidate summary (serve/pool): {"count": 0}
Recommendation picked summary (serve/pool): {"count": 0}
```

The UI displayed "还有 142 条可换" because `/api/runtime-status` exposed
`pool_available_count=142`. The actual `serve()` path loaded zero candidates.

Current code baseline:

- v0.3.57 already made `count_pool_candidates()` require
  `pool_expression` / `pool_topic_label`.
- v0.3.66 already made `count_pool_candidates()` require
  `style_key` / `topic_group`.
- `count_pool_candidates()` and `get_pool_candidates()` therefore already share
  the main readiness gates.
- The remaining confirmed drift is freshness: `get_pool_candidates()` calls
  `_ensure_fresh_read()` before querying SQLite/WAL state, but
  `count_pool_candidates()` does not.

In code, the current display number comes from `database.count_pool_candidates()`,
while a reshuffle uses `recommendation_engine.serve()` and then
`database.get_pool_candidates()`. These must not drift, especially within the
same user action.

## 2. Definitions

### 2.1 Servable Item

A row is **servable** only if it can be returned by `serve()` without fallback
or dead-link behavior.

Required row predicates:

```text
COALESCE(pool_status, 'fresh') = 'fresh'
COALESCE(feedback_type, '') != 'dislike'
COALESCE(pool_expression, '') != ''
COALESCE(pool_topic_label, '') != ''
COALESCE(style_key, '') != ''
COALESCE(topic_group, '') != ''
NOT EXISTS (SELECT 1 FROM recommendations r WHERE r.bvid = content_cache.bvid)
not recently viewed by content key
source is linkable according to `_is_linkable_pool_source(...)`
```

The recently viewed gate is intentionally time-windowed. A row can become
servable again after the viewed window expires, but count and load must evaluate
the same viewed window at read time.

For XHS, `_is_linkable_pool_source(...)` currently requires `content_url` to
contain `xsec_token=`.

### 2.2 Pending Pool Item

A row is **pending** when it is valid fresh material but missing one or more
servable requirements, most often:

- missing `style_key` / `topic_group` because `classify_pool_backlog` has not
  completed
- missing `pool_expression` / `pool_topic_label` because copy precompute has not
  completed
- XHS row missing an `xsec_token` and therefore not safely openable

Pending items may be useful inventory, but they must never be labeled as "可换".

### 2.3 Runtime Counts

`pool_available_count` keeps its public field name for compatibility, but its
meaning is tightened:

```text
pool_available_count = count of servable items
```

New optional fields:

```text
pool_raw_count      = count of fresh, not-disliked, not-recommended material before readiness gates
pool_pending_count  = independently counted non-viewed material still missing readiness/linkability gates
```

`pool_pending_count` must not be implemented as `pool_raw_count -
pool_available_count`: recently viewed rows are unavailable but are not pending.

If only one count can be implemented in the first patch, `pool_available_count`
must be the servable count. Raw/pending counts are allowed to follow in the
same PR if the implementation remains small.

## 3. Backend Design

### 3.1 Count/Load Parity

Keep `count_pool_candidates()` lightweight, but make its freshness/readiness
contract match `get_pool_candidates()`.

Recommended shape:

```python
def count_pool_candidates(self) -> int:
    """Return immediately servable pool candidates."""
    self._ensure_fresh_read()
    # Keep SELECT narrow: bvid/source/source_platform/content_url are enough
    # for viewed and linkability filtering.
    ...
```

Do not switch the count path to `SELECT *` just to share implementation with
`get_pool_candidates()`. If a future refactor introduces a shared helper, it
must preserve a narrow count projection or a dedicated count mode.

The contract is: count and load use the same freshness/readiness gates, and
tests cover parity for ready, not-ready, recently viewed, and stale snapshot
cases.

### 3.2 Fresh Read Requirement

Every pool availability count must call `_ensure_fresh_read()` before reading.

Reason: `get_pool_candidates()` already refreshes the SQLite/WAL snapshot, while
`count_pool_candidates()` currently does not. The same request can therefore
see a stale count and a fresh empty candidate list.

### 3.3 Runtime Status API

`ContinuousRefreshController.get_runtime_status()` must always expose the
correct servable count:

```json
{
  "pool_available_count": 0
}
```

If raw/pending diagnostics are included in the same PR, the screenshot/log
scenario should expose:

```json
{
  "pool_available_count": 0,
  "pool_pending_count": 142,
  "pool_raw_count": 142
}
```

for the screenshot/log scenario.

`pool_available_count` is the only field that means "can swap now".

### 3.4 Runtime Events

Every event that includes `pool_available_count` must use the same servable
count:

- `pool_status`
- `refresh.pool_updated`
- manual refresh completion events
- any future push event consumed by extension/mobile/desktop UIs

If pending/raw fields are available, include them in the same events.

### 3.5 Diagnostics

When `serve()` loads zero candidates, log the readiness breakdown, not just the
raw pool number.

Recommended log fields:

```text
raw=<n> servable=<n> pending=<n> missing_copy=<n> missing_classification=<n> unopenable=<n>
```

This avoids future ambiguity between "no material", "material is pending", and
"count/load query drift".

## 4. Frontend Contract

### 4.1 Extension Popup / Side Panel

`normalizeRuntimeStatus()` should keep normalizing `pool_available_count`, but
local variable names and render copy should treat it as servable.

Rules:

| Runtime state | UI copy | Button |
|---------------|---------|--------|
| `pool_available_count > 0` | `还有 N 条可换` | enabled |
| `pool_available_count == 0` and `pool_pending_count > 0` | `找到 N 条素材，正在整理成可换内容` | disabled or soft-disabled |
| both zero and refresh running | `正在补货` | disabled |
| both zero and idle | `这池先翻到头了，等后台再补点新的` | disabled |

The text "当前可换" must never be paired with raw or pending counts.

### 4.2 Mobile Web

Mobile web should use the same runtime status semantics. If mobile keeps its
own view-model helpers, port the same state table from the extension helper
instead of inventing separate copy.

The first viewport recommendation summary must distinguish:

- immediately swappable count
- pending material count
- refresh/running state

### 4.3 Desktop Web

Desktop web pool widgets must follow the same rule:

```text
visible "可换" number = pool_available_count = servable count
```

Raw/pending counts may be displayed as secondary operational copy, but never
as "可换".

### 4.4 Empty Reshuffle Safety

If a UI sends `POST /api/recommendations/reshuffle` or `/append` and receives
an empty item list while it believed `pool_available_count > 0`, it must:

1. refetch `/api/runtime-status`
2. replace local runtime status with the fresh payload
3. show a transient "池子状态刚刚同步，正在整理内容" style message

The UI should lock or debounce the reshuffle action during this refetch so a
second click cannot race against the corrected runtime state.

This is a defense-in-depth guard. The backend fix should make this rare.

## 5. Implementation Touchpoints

### Backend

| File | Expected change |
|------|-----------------|
| `src/openbiliclaw/storage/database.py` | `count_pool_candidates()` uses `_ensure_fresh_read()` and keeps the same gates as load without widening its projection |
| `src/openbiliclaw/runtime/refresh.py` | Runtime status/events use servable count; optional raw/pending counts |
| `src/openbiliclaw/api/models.py` | Add optional `pool_raw_count` / `pool_pending_count` to `RuntimeStatusResponse` |
| `src/openbiliclaw/recommendation/engine.py` | Improve zero-candidate diagnostics |

### Frontend

| Surface | Expected change |
|---------|-----------------|
| `extension/popup/*` | Render "可换" only from servable count; pending copy when servable is zero |
| `src/openbiliclaw/web/*` mobile files | Same runtime count semantics in mobile recommend view |
| `src/openbiliclaw/web/desktop/*` | Same runtime count semantics in desktop widgets |

### Docs

Because this changes runtime API semantics, update:

- `docs/modules/runtime.md`
- `docs/modules/recommendation.md`
- `docs/modules/extension.md`
- `docs/changelog.md`

## 6. Tests

### 6.1 Database Tests

Create or extend focused tests for pool readiness counts:

1. Fresh row missing `pool_expression` is not counted as servable.
2. Fresh row missing `pool_topic_label` is not counted as servable.
3. Fresh row missing `style_key` is not counted as servable.
4. Fresh row missing `topic_group` is not counted as servable.
5. XHS row without `xsec_token` is not counted as servable.
6. Recently viewed row is not counted until the viewed window expires.
7. Fully ready row is counted and returned by `get_pool_candidates()`.
8. Count and load stay consistent after another connection writes to the DB.

### 6.2 API Tests

Runtime status should return:

```json
{
  "pool_available_count": 0,
  "pool_pending_count": 142
}
```

when rows are fresh but not ready.

Runtime status should return:

```json
{
  "pool_available_count": 10,
  "pool_pending_count": 0
}
```

when ten rows are fully ready.

### 6.3 Frontend Unit Tests

For extension, mobile, and desktop helpers:

- `pool_available_count=142` renders "还有 142 条可换"
- `pool_available_count=0, pool_pending_count=142` does not render "142 条可换"
- `pool_available_count=0, pool_pending_count=142` renders pending/preparing copy
- swap/append empty response forces runtime status refetch

### 6.4 Manual / Browser Verification

Use one seeded DB scenario across all surfaces:

| Scenario | Expected extension | Expected mobile | Expected desktop |
|----------|--------------------|-----------------|------------------|
| 142 pending, 0 servable | no "142 条可换"; swap disabled/preparing | same | same |
| 10 servable, 132 pending | "还有 10 条可换" | same | same |
| 0 servable, 0 pending, refresh running | replenishing copy | same | same |

Screenshots should be attached for extension, mobile viewport, and desktop web.

## 7. Acceptance Criteria

- A user never sees "还有 N 条可换" unless `POST /api/recommendations/reshuffle`
  can return at least one item under the same DB state.
- `pool_available_count` means servable count in REST responses and runtime
  events.
- Raw/pending inventory is never labeled as "可换".
- The log line `serve(/pool) loaded 0 candidates from pool of N` no longer
  appears with a positive servable count.
- The screenshot failure mode becomes: "找到 142 条素材，正在整理成可换内容", not
  "还有 142 条可换".

## 8. Rollout Notes

This is safe to roll out backend-first:

1. Backend tightens `pool_available_count`; old frontends will display a smaller
   but truthful "可换" count.
2. Frontends add pending/raw copy when the new fields are present.
3. If older frontends do not know `pool_pending_count`, they still stop showing
   false positive availability once backend count is corrected.

This should be one PR if the implementation remains small. If raw/pending
breakdowns grow large, ship the servable `pool_available_count` fix first and
add richer pending diagnostics in a follow-up PR.
