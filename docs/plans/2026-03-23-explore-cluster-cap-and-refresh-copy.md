# Explore Cluster Cap And Refresh Copy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent a single explore subtopic cluster from flooding the pool, while making refresh status distinguish between “still running”, “discovered content”, and “net new immediately swappable inventory”.

**Architecture:** Add a lightweight high-risk explore-cluster cap around refresh/pool maintenance instead of building a full clustering system. Extend runtime status with a discovered-count field, then update popup wording to use refresh state plus both counts instead of overloading `last_replenished_count`.

**Tech Stack:** Python, pytest, Ruff, SQLite-backed runtime state, JavaScript popup helpers, Markdown docs

---

### Task 1: Lock the refresh status semantics with failing tests

**Files:**
- Modify: `tests/test_refresh_runtime.py`
- Modify: `extension/tests/popup-helpers.test.ts`
- Reference: `src/openbiliclaw/runtime/refresh.py`
- Reference: `extension/popup/popup-helpers.js`

**Step 1: Write the failing backend test**

Add a refresh-runtime test that simulates:

- refresh discovers items
- pool count before and after is unchanged

Assert:

- `last_discovered_count` is greater than 0
- `last_replenished_count` is 0

**Step 2: Write the failing popup test**

Add a popup-helper test that asserts:

- when `manual_refresh_state === "running"`, pool summary does not say `这轮还没补进`
- when refresh finished with discovered count > 0 and replenished count == 0, summary uses the new “找到了内容，但可换库存没变” style wording

**Step 3: Run tests to verify they fail**

Run:

- `./.venv/bin/pytest tests/test_refresh_runtime.py -k discovered_count -q`
- `node --test --experimental-strip-types extension/tests/popup-helpers.test.ts`

Expected: FAIL because the field and wording do not exist yet.

**Step 4: Write minimal implementation**

Update runtime state and popup helper logic to support `last_discovered_count` and state-sensitive wording.

**Step 5: Re-run tests**

Run the same commands and expect PASS.

### Task 2: Add explore high-risk cluster control with failing tests

**Files:**
- Modify: `tests/test_refresh_runtime.py`
- Modify: `tests/test_storage.py` or a more targeted pool-maintenance test file if needed
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Modify: `src/openbiliclaw/storage/database.py`

**Step 1: Write the failing test**

Add a test that seeds fresh pool data with overrepresented `explore` items in a manufacturing-style cluster and asserts the refresh/pool-maintenance step does not keep that cluster above the configured cap.

**Step 2: Run the test to verify it fails**

Run the targeted pytest command for the new test.

**Step 3: Write minimal implementation**

Implement:

- lightweight cluster labeling for high-risk explore topics
- cap enforcement against overrepresented fresh-pool explore clusters
- a gentle downgrade path for overflow items so they stop dominating `reshuffle`

**Step 4: Re-run the targeted test**

Expected: PASS

### Task 3: Update module docs and changelog

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Document the behavior**

Record:

- explore high-risk cluster cap
- refresh status now separates discovered count from net added count
- popup no longer uses “这轮还没补进” during running refresh

**Step 2: Check patch formatting**

Run:

`git diff --check -- docs/modules/discovery.md docs/modules/recommendation.md docs/modules/extension.md docs/changelog.md`

Expected: no output

### Task 4: Final verification

**Files:**
- Verify: `src/openbiliclaw/runtime/refresh.py`
- Verify: `src/openbiliclaw/storage/database.py`
- Verify: `extension/popup/popup-helpers.js`
- Verify: tests touched above

**Step 1: Run targeted tests**

Run:

- `./.venv/bin/pytest tests/test_refresh_runtime.py -q`
- `./.venv/bin/pytest tests/test_storage.py -q`
- `node --test --experimental-strip-types extension/tests/popup-helpers.test.ts`

**Step 2: Run lint on touched files**

Run:

- `./.venv/bin/ruff check src/openbiliclaw/runtime/refresh.py src/openbiliclaw/storage/database.py tests/test_refresh_runtime.py tests/test_storage.py`

**Step 3: Patch check**

Run:

`git diff --check -- src/openbiliclaw/runtime/refresh.py src/openbiliclaw/storage/database.py extension/popup/popup-helpers.js tests/test_refresh_runtime.py tests/test_storage.py extension/tests/popup-helpers.test.ts docs/modules/discovery.md docs/modules/recommendation.md docs/modules/extension.md docs/changelog.md`

Expected: no output
