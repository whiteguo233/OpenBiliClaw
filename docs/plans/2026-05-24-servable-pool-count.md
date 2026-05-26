# Servable Pool Count Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make every extension, mobile web, and desktop web "可换" count reflect only recommendations the backend can serve immediately.

**Architecture:** Tighten `pool_available_count` to mean servable count everywhere. The database keeps the existing lightweight count query, adds the same fresh-read behavior as the load path, and preserves the existing readiness gates. Runtime APIs/events expose that servable count plus optional raw/pending counts; all clients render "可换" only from the servable count.

**Tech Stack:** Python 3.12, SQLite, FastAPI/Pydantic, pytest, Vanilla JS ES modules, extension TypeScript tests run by `node --test --experimental-strip-types`.

---

Implements: `docs/plans/2026-05-24-servable-pool-count-spec.md`

## Task 1: Database Servable Count Contract

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: extend `tests/test_storage.py`

**Step 1: Write failing database tests**

Create tests that initialize a temporary `Database`, insert representative `content_cache` rows, and assert count/load parity. The missing ready-field cases already have coverage in current storage tests; keep or extend those as regression coverage, but the new failing case should target stale read freshness.

Required cases:

```python
def test_pool_count_excludes_rows_missing_ready_fields(db):
    # Insert fresh rows missing one of:
    # pool_expression, pool_topic_label, style_key, topic_group.
    # Assert count_pool_candidates() == 0
    # Assert get_pool_candidates(limit=10) == []
```

```python
def test_pool_count_includes_only_servable_rows(db):
    # Insert one row with all required fields and a linkable URL.
    # Assert count_pool_candidates() == 1
    # Assert len(get_pool_candidates(limit=10)) == 1
```

```python
def test_pool_count_refreshes_stale_read_snapshot(tmp_path):
    # Open Database A.
    # Start a read snapshot by calling count_pool_candidates().
    # Open Database B on the same file and mark the row recommended/shown
    # or insert into recommendations.
    # Assert Database A count_pool_candidates() reflects the new state.
```

Also include a viewed-window regression:

```python
def test_pool_count_excludes_recently_viewed_rows(db):
    # Insert one fully ready row and mark it recently viewed.
    # Assert count_pool_candidates() == 0
    # Assert get_pool_candidates(limit=10) == []
```

**Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_storage.py -v
```

Expected: the stale snapshot test fails before implementation. Existing missing-field gate tests should already pass.

**Step 3: Add fresh-read to the lightweight count path**

In `Database.count_pool_candidates()`, add `self._ensure_fresh_read()` before the SQL query.

Keep the existing narrow projection:

```sql
SELECT bvid, source, source_platform, content_url
```

Do not change count to `SELECT *` or route it through a helper that materializes full candidate rows. The count path only needs enough fields for viewed and linkability filtering.

Recommended implementation:

```python
def count_pool_candidates(self) -> int:
    self._ensure_fresh_read()
    cursor = self.conn.execute(...)
    ...
```

Important: keep `count_pool_candidates()` as the public method so existing callers continue working, but document that it now means "servable".

**Step 4: Run database tests**

Run:

```bash
pytest tests/test_storage.py -v
```

Expected: all new tests pass.

**Step 5: Run full backend regression**

Run:

```bash
pytest
```

Expected: pass. This matters because `_ensure_fresh_read()` can affect tests that use mock database fixtures or open transactions.

**Step 6: Commit**

```bash
git add src/openbiliclaw/storage/database.py tests/test_storage.py
git commit -m "fix: make pool count match servable candidates"
```

## Task 2: Runtime API Counts And Events

This task is required for richer pending/material copy. The correctness
guarantee for "可换" still comes from Task 1 because all clients already consume
`pool_available_count`.

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/api/models.py`
- Test: `tests/test_refresh_runtime.py`
- Test: `tests/test_runtime_events.py`

**Step 1: Write failing runtime tests**

Add tests for runtime status and event payloads:

```python
def test_runtime_status_reports_servable_and_pending_counts(...):
    # Seed pending rows and zero servable rows.
    # Assert payload["pool_available_count"] == 0
    # Assert payload["pool_pending_count"] > 0
```

```python
def test_pool_status_event_uses_servable_count(...):
    # Publish/update pool status after seeded pending rows.
    # Assert event["pool_available_count"] == 0
```

**Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_refresh_runtime.py tests/test_runtime_events.py -v
```

Expected: new pending fields missing or count semantics not yet wired.

**Step 3: Add optional pending/raw fields**

Update `RuntimeStatusResponse`:

```python
pool_raw_count: int = 0
pool_pending_count: int = 0
```

Add a small database helper if needed. Do not compute pending as `raw - available`; recently viewed rows are unavailable but not pending.

```python
def count_pool_readiness(self) -> dict[str, int]:
    return {
        "available": self.count_pool_candidates(),
        "raw": raw_count,
        "pending": pending_count,
    }
```

`pending_count` should be counted independently from rows that are fresh,
not disliked, not recommended, not recently viewed, but fail one or more
readiness/linkability gates such as missing copy, missing classification, or an
unopenable source URL.

Use that helper in:

- `ContinuousRefreshController.get_runtime_status()`
- `_publish_pool_status_if_changed()`
- refresh completion payloads that currently send `pool_available_count`

Keep old clients compatible by preserving `pool_available_count`.

**Step 4: Run runtime tests**

Run:

```bash
pytest tests/test_refresh_runtime.py tests/test_runtime_events.py -v
```

Expected: pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/runtime/refresh.py src/openbiliclaw/api/models.py tests/test_refresh_runtime.py tests/test_runtime_events.py
git commit -m "feat: expose servable and pending pool counts"
```

## Task 3: Recommendation Diagnostics

**Files:**
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Test: `tests/test_recommendation_engine.py`

**Step 1: Write failing diagnostic test**

Add or extend a test that triggers `serve()` with no candidates and asserts the warning includes readiness counts.

Expected warning fields:

```text
servable=0
pending=<n>
raw=<n>
```

**Step 2: Run test and verify failure**

Run:

```bash
pytest tests/test_recommendation_engine.py -v
```

Expected: warning format does not include readiness breakdown yet.

**Step 3: Update warning**

In `RecommendationEngine.serve()`, keep existing candidate summary logs but replace the ambiguous warning with counts from the database readiness helper or direct lightweight readiness count methods.

Do not change ranking or serving behavior.

**Step 4: Run recommendation tests**

Run:

```bash
pytest tests/test_recommendation_engine.py -v
```

Expected: pass.

**Step 5: Commit**

```bash
git add src/openbiliclaw/recommendation/engine.py tests/test_recommendation_engine.py
git commit -m "chore: add pool readiness diagnostics"
```

## Task 4: Extension Popup Semantics

**Files:**
- Modify: `extension/popup/popup-helpers.js`
- Modify as needed: `extension/popup/popup.js`
- Test: `extension/tests/popup-helpers.test.ts`
- Test: `extension/tests/popup-copy.test.ts`

**Step 1: Write failing extension helper tests**

Add cases:

```js
const zeroSummary = getPoolStatusSummary({
  initialized: true,
  pool_available_count: 0,
  pool_pending_count: 142,
  manual_refresh_state: "running",
});
assert(!zeroSummary.available.includes("142 条可换"));
```

```js
const readySummary = getPoolStatusSummary({
  initialized: true,
  pool_available_count: 10,
  pool_pending_count: 132,
});
assert(readySummary.available.includes("还有 10 条可换"));
```

**Step 2: Run tests and verify failure**

Run from `extension/`:

```bash
npm test -- --test-name-pattern=pool
```

If that pattern is too narrow for changed files, run:

```bash
npm test
```

Expected: pending copy test fails.

**Step 3: Update popup helpers**

Change `normalizeRuntimeStatus()` to include:

```js
pool_raw_count: Number(status?.pool_raw_count ?? 0),
pool_pending_count: Number(status?.pool_pending_count ?? 0),
```

Change `getPoolStatusSummary()`:

- `available` uses only `pool_available_count`
- pending state uses `pool_pending_count`
- never render pending count as "可换"

**Step 4: Add empty reshuffle guard if missing**

If `reshuffleRecommendations()` returns `items: []` while the local state had `pool_available_count > 0`, refetch runtime status and re-render the status row.

Lock or debounce the reshuffle action while the refetch is in flight so repeated clicks cannot race against the corrected runtime status.

**Step 5: Run extension tests**

Run targeted popup tests and the repo's standard extension test subset.

Expected: pass.

**Step 6: Commit**

```bash
git add extension/popup/popup-helpers.js extension/popup/popup.js extension/tests/popup-helpers.test.ts extension/tests/popup-copy.test.ts
git commit -m "fix: show only servable pool count in extension"
```

## Task 5: Mobile Web Semantics

**Files:**
- Modify: `src/openbiliclaw/web/js/view-models.js`
- Modify: `src/openbiliclaw/web/js/views/recommend.js`
- Test: `tests/test_mobile_web_view_models.py`

**Step 1: Locate mobile runtime status renderer**

Run:

```bash
rg -n "pool_available_count|可换|poolChips|getPoolStatusSummary" src/openbiliclaw/web/js
```

Confirm the count is rendered through `view-models.js` before editing.

**Step 2: Write failing mobile tests**

Extend Node-backed view-model tests:

```js
const summary = getPoolStatusSummary({
  initialized: true,
  pool_available_count: 0,
  pool_pending_count: 142,
});
assert(!summary.available.includes("142 条可换"));
```

Also test a servable case with `pool_available_count: 10`.

**Step 3: Run test and verify failure**

Run:

```bash
pytest tests/test_mobile_web_view_models.py -v
```

Expected: missing pending semantics fail.

**Step 4: Update mobile view models**

Normalize `pool_raw_count` and `pool_pending_count`.

Render the same semantic table as extension:

- available > 0: "还有 N 条可换"
- available == 0 and pending > 0: "找到 N 条素材，正在整理成可换内容"
- empty/running: replenishment copy

**Step 5: Run mobile tests**

Run:

```bash
pytest tests/test_mobile_web_view_models.py -v
```

Expected: pass.

**Step 6: Commit**

```bash
git add src/openbiliclaw/web/js/view-models.js src/openbiliclaw/web/js/views/recommend.js tests/test_mobile_web_view_models.py
git commit -m "fix: show servable pool count on mobile web"
```

## Task 6: Desktop Web Semantics

**Files:**
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify if needed: `src/openbiliclaw/web/desktop/index.html`
- Test: add coverage to an existing JS/browser-oriented test if present, otherwise document manual verification

**Step 1: Locate desktop runtime status renderer**

Find where desktop web renders pool count from runtime status.

Run:

```bash
rg -n "pool_available_count|可换|runtimeStatus|runtime-status" src/openbiliclaw/web/desktop
```

**Step 2: Add or update test coverage**

If desktop JS has an existing test harness, add the same two cases:

- 0 servable + 142 pending does not render "142 条可换"
- 10 servable renders "还有 10 条可换"

If no harness exists, add this to manual verification in the PR.

**Step 3: Update desktop renderer**

Use `pool_available_count` for "可换" only and show pending copy separately when present.

**Step 4: Verify**

Run available web/desktop tests. If no automated coverage exists, use browser smoke verification with seeded API responses.

**Step 5: Commit**

```bash
git add src/openbiliclaw/web/desktop/assets/js/app.js src/openbiliclaw/web/desktop/index.html
git commit -m "fix: show servable pool count on desktop web"
```

## Task 7: Documentation And Final Verification

**Files:**
- Modify: `docs/modules/runtime.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

Document:

- `pool_available_count` now means servable count
- `pool_pending_count` means material not yet ready
- "可换" UI copy must only use servable count

**Step 2: Run backend checks**

Run:

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/
pytest
```

Expected: all pass.

**Step 3: Run extension checks**

Run the repo's extension test command or targeted extension tests changed above.

Expected: all changed extension tests pass.

**Step 4: Manual verification**

Seed or mock three runtime states and capture extension/mobile/desktop screenshots:

| State | Expected |
|-------|----------|
| `available=0`, `pending=142` | no "142 条可换" |
| `available=10`, `pending=132` | "还有 10 条可换" |
| `available=0`, `pending=0`, refresh running | replenishing copy |

**Step 5: Commit docs**

```bash
git add docs/modules/runtime.md docs/modules/recommendation.md docs/modules/extension.md docs/changelog.md
git commit -m "docs: document servable pool count contract"
```
