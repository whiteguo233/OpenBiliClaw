/**
 * Tests for the Douyin background task dispatcher's pure helpers.
 *
 * Task 5 of the Douyin bootstrap import plan
 * (docs/plans/2026-05-06-douyin-bootstrap-import.md).
 *
 * Module isolation: zero imports from extension/src/background/xhs-task-dispatcher.
 * The orchestration (chrome.tabs / chrome.runtime / fetch lifecycle) lives in
 * the dispatcher module but isn't unit-tested here — Task 4's chrome-devtools
 * MCP probe already validated the highest-risk seam (fetch-tap in real Chrome).
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildDyTaskUrl,
  buildDyExecuteMessageData,
  computeDyTaskTimeoutMs,
  isValidDyTask,
  onTabReady,
  pollDyTaskNow,
  shouldOpenDyTaskActive,
  shouldFinalizeHotTask,
} from "../src/background/dy-task-dispatcher.ts";

test("buildDyTaskUrl routes bootstrap_profile to the douyin home", () => {
  // The content-script executor will navigate from this initial URL to
  // the per-scope tabs (handled by buildScopeUrl in dy/task-executor.ts).
  // The dispatcher just needs to land us on a douyin.com tab where the
  // SDK + RENDER_DATA are available.
  assert.equal(
    buildDyTaskUrl({ id: "t", type: "bootstrap_profile" }),
    "https://www.douyin.com/",
  );
});

test("buildDyTaskUrl routes search task to the douyin home", () => {
  assert.equal(
    buildDyTaskUrl({ id: "t-search", type: "search", keywords: ["猫"] }),
    "https://www.douyin.com/",
  );
});

test("buildDyTaskUrl routes hot task to the douyin home", () => {
  assert.equal(
    buildDyTaskUrl({
      id: "t-hot",
      type: "hot",
      hot_items: [{ word: "热点词", sentence_id: "2495363" }],
    }),
    "https://www.douyin.com/",
  );
});

test("buildDyTaskUrl routes feed task to the douyin home", () => {
  assert.equal(
    buildDyTaskUrl({ id: "t-feed", type: "feed", max_items: 10 }),
    "https://www.douyin.com/",
  );
});

test("buildDyTaskUrl returns null for unknown task types", () => {
  assert.equal(buildDyTaskUrl({ id: "t", type: "unknown" as never }), null);
});

test("shouldOpenDyTaskActive only foregrounds bootstrap imports", () => {
  assert.equal(shouldOpenDyTaskActive({ id: "bootstrap", type: "bootstrap_profile" }), true);
  assert.equal(shouldOpenDyTaskActive({ id: "search", type: "search", keywords: ["猫"] }), false);
  assert.equal(
    shouldOpenDyTaskActive({
      id: "hot",
      type: "hot",
      hot_items: [{ word: "热点词", sentence_id: "2495363" }],
    }),
    false,
  );
  assert.equal(shouldOpenDyTaskActive({ id: "feed", type: "feed", max_items: 10 }), false);
});

test("isValidDyTask accepts bootstrap_profile with optional payload fields", () => {
  assert.equal(
    isValidDyTask({
      id: "abc",
      type: "bootstrap_profile",
      scopes: ["dy_post", "dy_collect", "dy_like", "dy_follow"],
      max_items_per_scope: 300,
      max_scroll_rounds: 15,
      max_stagnant_scroll_rounds: 5,
    }),
    true,
  );
  // Minimal valid task — id + type only.
  assert.equal(isValidDyTask({ id: "abc", type: "bootstrap_profile" }), true);
});

test("isValidDyTask accepts search with non-empty keywords", () => {
  assert.equal(
    isValidDyTask({
      id: "search-abc",
      type: "search",
      keywords: ["猫", "美食"],
      max_items_per_keyword: 10,
    }),
    true,
  );
});

test("isValidDyTask accepts hot with sentence_id hot items", () => {
  assert.equal(
    isValidDyTask({
      id: "hot-abc",
      type: "hot",
      hot_items: [{ word: "热点词", sentence_id: "2495363" }],
      max_items_per_hot: 10,
    }),
    true,
  );
});

test("isValidDyTask accepts feed with max_items", () => {
  assert.equal(
    isValidDyTask({
      id: "feed-abc",
      type: "feed",
      max_items: 10,
    }),
    true,
  );
});

test("isValidDyTask rejects malformed input", () => {
  assert.equal(isValidDyTask(null), false);
  assert.equal(isValidDyTask("string"), false);
  assert.equal(isValidDyTask({}), false);
  assert.equal(isValidDyTask({ id: "" }), false);
  assert.equal(isValidDyTask({ id: "x", type: "search" }), false);
  assert.equal(isValidDyTask({ id: "x", type: "search", keywords: [] }), false);
  assert.equal(isValidDyTask({ id: "x", type: "hot" }), false);
  assert.equal(isValidDyTask({ id: "x", type: "hot", hot_items: [] }), false);
  assert.equal(isValidDyTask({ id: "x", type: "feed", max_items: 0 }), false);
  assert.equal(isValidDyTask({ id: "x", type: "bootstrap_profile", scopes: "not-array" }), false);
  // Unknown scope name slips into the array — must be rejected so we
  // never end up firing buildScopeUrl on an unsupported scope.
  assert.equal(
    isValidDyTask({ id: "x", type: "bootstrap_profile", scopes: ["dy_post", "dy_unknown"] }),
    false,
  );
});

test("computeDyTaskTimeoutMs scales with max_scroll_rounds × number of scopes", () => {
  // Default (no rounds): the floor — 30s — to give the executor time to
  // read RENDER_DATA + extract sec_uid even if there's nothing to scroll.
  assert.equal(
    computeDyTaskTimeoutMs({ id: "t", type: "bootstrap_profile" }),
    30_000,
  );
  // 15 rounds × 4 scopes × 3s/round + 30s base = 30s + 180s = 210s,
  // but capped at the BOOTSTRAP_MAX_TASK_TIMEOUT_MS = 360s.
  const big = computeDyTaskTimeoutMs({
    id: "t",
    type: "bootstrap_profile",
    max_scroll_rounds: 15,
    scopes: ["dy_post", "dy_collect", "dy_like", "dy_follow"],
  });
  assert.ok(big >= 30_000, `expected >= 30s, got ${big}`);
  assert.ok(big <= 360_000, `expected <= 360s ceiling, got ${big}`);
  assert.ok(big > 60_000, `15 rounds × 4 scopes should clear 60s, got ${big}`);
});

test("computeDyTaskTimeoutMs falls back to 4-scope assumption when scopes omitted", () => {
  // The dispatcher may receive a task that doesn't enumerate scopes
  // (CLI default). We compute timeout assuming all four scopes will run.
  const timeout = computeDyTaskTimeoutMs({
    id: "t",
    type: "bootstrap_profile",
    max_scroll_rounds: 5,
  });
  assert.ok(timeout > 30_000, `expected > 30s with 5 rounds, got ${timeout}`);
});

test("computeDyTaskTimeoutMs gives search enough time for page signing", () => {
  const timeout = computeDyTaskTimeoutMs({
    id: "search-timeout",
    type: "search",
    keywords: ["科技"],
  });

  assert.ok(timeout >= 180_000, `expected at least 180s for search, got ${timeout}`);
  assert.ok(timeout <= 360_000, `expected <= 360s ceiling, got ${timeout}`);
});

test("computeDyTaskTimeoutMs scales with hot item count", () => {
  const timeout = computeDyTaskTimeoutMs({
    id: "hot-timeout",
    type: "hot",
    hot_items: [
      { word: "热点 1", sentence_id: "1" },
      { word: "热点 2", sentence_id: "2" },
    ],
  });

  assert.ok(timeout >= 120_000, `expected at least 120s for two hot terms, got ${timeout}`);
  assert.ok(timeout <= 360_000, `expected <= 360s ceiling, got ${timeout}`);
});

test("computeDyTaskTimeoutMs gives feed enough time for signed API harvest", () => {
  const timeout = computeDyTaskTimeoutMs({ id: "feed-timeout", type: "feed", max_items: 10 });

  assert.ok(timeout >= 60_000, `expected at least 60s for feed harvest, got ${timeout}`);
  assert.ok(timeout <= 360_000, `expected <= 360s ceiling, got ${timeout}`);
});

test("buildDyExecuteMessageData includes only the fields the executor needs", () => {
  const data = buildDyExecuteMessageData({
    id: "task-99",
    type: "bootstrap_profile",
    scopes: ["dy_post", "dy_collect"],
    max_items_per_scope: 300,
    max_scroll_rounds: 15,
    max_stagnant_scroll_rounds: 5,
  });
  assert.equal(data.task_id, "task-99");
  assert.equal(data.type, "bootstrap_profile");
  assert.deepEqual(data.scopes, ["dy_post", "dy_collect"]);
  assert.equal(data.max_items_per_scope, 300);
  assert.equal(data.max_scroll_rounds, 15);
  assert.equal(data.max_stagnant_scroll_rounds, 5);
});

test("buildDyExecuteMessageData omits undefined fields (no leaking nullish payload)", () => {
  const data = buildDyExecuteMessageData({ id: "t", type: "bootstrap_profile" });
  assert.equal(data.task_id, "t");
  assert.equal(data.type, "bootstrap_profile");
  assert.equal("scopes" in data, false);
  assert.equal("max_items_per_scope" in data, false);
  assert.equal("max_scroll_rounds" in data, false);
});

test("buildDyExecuteMessageData includes hot task payload", () => {
  const data = buildDyExecuteMessageData({
    id: "hot-task",
    type: "hot",
    hot_items: [{ word: "热点词", sentence_id: "2495363" }],
    max_items_per_hot: 8,
    max_items: 3,
  });

  assert.equal(data.task_id, "hot-task");
  assert.equal(data.type, "hot");
  assert.deepEqual(data.hot_items, [{ word: "热点词", sentence_id: "2495363" }]);
  assert.equal(data.max_items_per_hot, 8);
  assert.equal(data.max_items, 3);
});

test("shouldFinalizeHotTask stops after enough hot related items", () => {
  assert.equal(
    shouldFinalizeHotTask({
      accumulatedCount: 3,
      maxItemsTotal: 3,
      currentHotIndex: 0,
      hotItemCount: 3,
    }),
    true,
  );
  assert.equal(
    shouldFinalizeHotTask({
      accumulatedCount: 1,
      maxItemsTotal: 3,
      currentHotIndex: 0,
      hotItemCount: 3,
    }),
    false,
  );
  assert.equal(
    shouldFinalizeHotTask({
      accumulatedCount: 1,
      maxItemsTotal: 3,
      currentHotIndex: 2,
      hotItemCount: 3,
    }),
    true,
  );
});

test("buildDyExecuteMessageData includes feed task payload", () => {
  const data = buildDyExecuteMessageData({
    id: "feed-task",
    type: "feed",
    max_items: 8,
  });

  assert.equal(data.task_id, "feed-task");
  assert.equal(data.type, "feed");
  assert.equal(data.max_items, 8);
});

test("pollDyTaskNow exists as the WS-driven immediate-poll entry point", () => {
  // Service-worker.ts calls this from runtimeSocket.onmessage when
  // backend broadcasts `dy_task_available`. We can't exercise the
  // chrome.tabs lifecycle here (no chrome global, no fetch backend),
  // but we MUST guarantee the export shape so the wire stays intact.
  assert.equal(typeof pollDyTaskNow, "function");
  // Calling it without chrome / network must not throw — pollNextTask
  // catches its own fetch errors so the dispatcher stays alive.
  assert.doesNotThrow(() => pollDyTaskNow());
});

test("onTabReady continues immediately when the tab is already complete", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const listeners: Array<(tabId: number, info: { status?: string }) => void> = [];
  let callbackCount = 0;

  (globalThis as { chrome?: unknown }).chrome = {
    tabs: {
      get: async (tabId: number) => ({ id: tabId, status: "complete" }),
      onUpdated: {
        addListener(listener: (tabId: number, info: { status?: string }) => void) {
          listeners.push(listener);
        },
        removeListener(listener: (tabId: number, info: { status?: string }) => void) {
          const index = listeners.indexOf(listener);
          if (index >= 0) listeners.splice(index, 1);
        },
      },
    },
  };

  try {
    onTabReady(42, () => {
      callbackCount += 1;
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(callbackCount, 1);
    assert.equal(listeners.length, 0);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
  }
});

test("onTabReady uses a fallback timer when Chrome never reports complete", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const listeners: Array<(tabId: number, info: { status?: string }) => void> = [];
  let callbackCount = 0;

  (globalThis as { chrome?: unknown }).chrome = {
    tabs: {
      get: async (tabId: number) => ({ id: tabId, status: "loading" }),
      onUpdated: {
        addListener(listener: (tabId: number, info: { status?: string }) => void) {
          listeners.push(listener);
        },
        removeListener(listener: (tabId: number, info: { status?: string }) => void) {
          const index = listeners.indexOf(listener);
          if (index >= 0) listeners.splice(index, 1);
        },
      },
    },
  };

  try {
    onTabReady(
      7,
      () => {
        callbackCount += 1;
      },
      { fallbackMs: 1 },
    );
    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.equal(callbackCount, 1);
    assert.equal(listeners.length, 0);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
  }
});
