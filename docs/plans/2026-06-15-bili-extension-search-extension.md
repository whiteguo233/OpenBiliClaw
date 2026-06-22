# Bili Extension Search Extension Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete the browser-extension half of Bilibili extension search fallback and verify it with real rendered Bilibili search pages.

**Architecture:** Keep backend API search as the primary path. When the backend broadcasts or polls a pending `bili_tasks(type="search")` task, the extension opens a background Bilibili search page, waits for rendered cards, scrapes visible video metadata from the DOM, and posts the result to `/api/sources/bili/task-result`. The result still flows through the backend `DiscoveryCandidatePipeline`; the extension never writes recommendations directly.

**Tech Stack:** Chrome MV3 service worker, TypeScript content scripts, esbuild extension bundle, Node `node:test`, existing backend `bili_tasks` endpoints.

---

### Task 1: Dispatcher Tests

**Files:**
- Create: `extension/tests/bili-task-dispatcher.test.ts`
- Create: `extension/src/background/bili-task-dispatcher.ts`

**Steps:**
1. Write failing tests for `buildBiliTaskUrl()`, `isValidBiliTask()`, `computeBiliTaskTimeoutMs()`, `buildBiliExecuteMessageData()`, and `pollBiliTaskNow()`.
2. Run: `cd extension && npm test -- tests/bili-task-dispatcher.test.ts`; expected: fails because the dispatcher module is missing.
3. Implement the pure helpers and minimal polling/export shell.
4. Run the dispatcher test until green.

### Task 2: DOM Executor Tests

**Files:**
- Create: `extension/tests/bili-task-executor.test.ts`
- Create: `extension/src/content/bili/task-executor.ts`
- Modify: `extension/src/content/bilibili.ts`

**Steps:**
1. Write failing tests for Bilibili search result extraction from card-like fake DOM nodes, BV extraction, play-count normalization, dedupe, max-item cap, and `executeBiliSearch()` empty/ok statuses.
2. Run: `cd extension && npm test -- tests/bili-task-executor.test.ts`; expected: fails because the executor module is missing.
3. Implement pure extractors plus the `BILI_TASK_EXECUTE` message listener.
4. Wire `installBiliMessageListener()` from the existing Bilibili content-script entry.
5. Run the executor test until green.

### Task 3: Service Worker Wiring

**Files:**
- Modify: `extension/src/background/service-worker.ts`
- Test: `extension/tests/bili-task-dispatcher.test.ts`

**Steps:**
1. Extend dispatcher tests to assert `pollBiliTaskNow()` is exported and safe without Chrome/network.
2. Import Bili dispatcher into the service worker.
3. Start polling on install/startup/cold start, handle `bili_task_available` runtime-stream events, route `BILI_TASK_RESULT`, and pass Bili alarms to `handleBiliTaskAlarm()`.
4. Run focused extension tests.

### Task 4: Docs

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/bilibili.md`
- Modify: `docs/changelog.md`
- Modify as needed if data-flow wording changes: `docs/architecture.md`, `docs/spec.md`, `README.md`, `README_EN.md`

**Steps:**
1. Update extension docs with the Bili task bridge, payload, DOM scraping contract, manual verification steps, and known limits.
2. Update Bilibili/runtime docs that previously said extension side remained Phase 2.
3. Add a changelog bullet under the current top version.

### Task 5: Verification and Real E2E

**Files:**
- No production edits expected.

**Steps:**
1. Run: `cd extension && npm test`.
2. Run: `cd extension && npm run typecheck`.
3. Run: `cd extension && npm run build`.
4. Run backend focused tests if any Python API surface changed; otherwise run `ruff check src/ tests/`, `mypy src/`, and focused Bili backend tests.
5. With the real backend restarted on this branch and the built extension loaded, enqueue or wait for a real Bili search task, verify the extension opens `search.bilibili.com/all?keyword=...`, posts real rendered videos, and the backend admits/cleans expected candidates.
