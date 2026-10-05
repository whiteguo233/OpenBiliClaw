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
  buildDyHotTerminalFailure,
  buildDyDiscoveryPageUrl,
  buildDyTaskUrl,
  buildDyExecuteMessageData,
  computeDyTaskTimeoutMs,
  dyScopeDegradedReason,
  executeTask,
  finalizeDyBootstrapStatus,
  isDySearchResultUrl,
  isValidDyTask,
  onTabReady,
  postDyNativeSaveResult,
  postDyTaskResult,
  pollDyTaskOnce,
  pollDyTaskNow,
  shouldOpenDyTaskActive,
  shouldFinalizeHotTask,
  shouldRetryDyFeedCapture,
} from "../src/background/dy-task-dispatcher.ts";
import type { NativeSaveResult, NativeSaveTask } from "../src/shared/native-save.ts";

const nativeTask: NativeSaveTask = {
  id: "123e4567-e89b-42d3-a456-426614174012",
  type: "native_save",
  platform: "douyin",
  platform_slug: "dy",
  item_key: "douyin:7300000000000000000",
  content_id: "7300000000000000000",
  content_url: "https://www.douyin.com/video/7300000000000000000",
  content_type: "video",
  requested_action: "favorite",
  resolved_action: "favorite",
  target_label: "抖音收藏",
};

type DispatcherMutexGlobal = typeof globalThis & {
  __OBC_DISPATCHER_MUTEX_HOLDER__?: string;
  __OBC_DISPATCHER_MUTEX_HELD_SINCE__?: number;
};

function clearDispatcherMutex(): void {
  const shared = globalThis as DispatcherMutexGlobal;
  delete shared.__OBC_DISPATCHER_MUTEX_HOLDER__;
  delete shared.__OBC_DISPATCHER_MUTEX_HELD_SINCE__;
}

test("finalizeDyBootstrapStatus preserves terminal degraded scope health", () => {
  assert.equal(
    finalizeDyBootstrapStatus({
      dy_post: "ok",
      dy_collect: "empty",
      dy_like: "ok",
      dy_follow: "ok",
    }),
    "ok",
  );
  assert.equal(
    finalizeDyBootstrapStatus({
      dy_post: "ok",
      dy_collect: "degraded",
      dy_like: "ok",
      dy_follow: "ok",
    }),
    "degraded",
  );
  assert.equal(
    finalizeDyBootstrapStatus({
      dy_post: "failed",
      dy_collect: "empty",
    }),
    "degraded",
  );
  assert.equal(
    dyScopeDegradedReason({
      task_id: "task-1",
      scope: "dy_collect",
      items: [],
      scope_count: 0,
      status: "degraded",
      error: "pagination_cursor_not_advanced",
    }),
    "dy_collect:pagination_cursor_not_advanced",
  );
});

test("dy task native_save union and dispatcher close through the exact authenticated result contract", async () => {
  assert.equal(isValidDyTask(nativeTask), true);
  assert.equal(isValidDyTask({ ...nativeTask, platform: "xiaohongshu", platform_slug: "xhs" }), false);
  assert.equal(buildDyTaskUrl(nativeTask), nativeTask.content_url);
  const result: NativeSaveResult = {
    task_id: nativeTask.id,
    item_key: nativeTask.item_key,
    status: "synced",
    error_code: "",
    error_message: "",
  };
  const calls: unknown[] = [];
  await executeTask(nativeTask, {
    run: async (receivedTask, slug, postResult) => {
      calls.push([receivedTask, slug]);
      await postResult(result);
    },
    postResult: async (received) => { calls.push(received); },
  });
  assert.deepEqual(calls, [[nativeTask, "dy"], result]);

  const requests: unknown[] = [];
  await postDyNativeSaveResult(result, {
    resolveUrl: async (path) => `http://127.0.0.1:8420/api${path}`,
    fetch: async (input, init) => { requests.push([input, init]); },
  });
  assert.deepEqual(requests, [[
    "http://127.0.0.1:8420/api/sources/dy/task-result",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(result),
    },
  ]]);
});

test("buildDyTaskUrl routes bootstrap_profile to the douyin home", () => {
  // The content-script executor will navigate from this initial URL to
  // the per-scope tabs (handled by buildScopeUrl in dy/task-executor.ts).
  // The dispatcher just needs to land us on a douyin.com tab where the
  // SDK + RENDER_DATA are available.
  assert.equal(
    buildDyTaskUrl({ id: "t", type: "bootstrap_profile" }),
    "https://www.douyin.com/?openbiliclaw_dy_task=1",
  );
});

test("buildDyTaskUrl routes search task to the douyin home", () => {
  assert.equal(
    buildDyTaskUrl({ id: "t-search", type: "search", keywords: ["猫"] }),
    "https://www.douyin.com/?openbiliclaw_dy_task=1",
  );
});

test("buildDyTaskUrl routes hot task to the douyin home", () => {
  assert.equal(
    buildDyTaskUrl({
      id: "t-hot",
      type: "hot",
      hot_items: [{ word: "热点词", sentence_id: "2495363" }],
    }),
    "https://www.douyin.com/?openbiliclaw_dy_task=1",
  );
});

test("buildDyTaskUrl routes feed task to the douyin home", () => {
  assert.equal(
    buildDyTaskUrl({ id: "t-feed", type: "feed", max_items: 10 }),
    "https://www.douyin.com/?openbiliclaw_dy_task=1",
  );
});

test("discovery task page URLs stay on douyin home", () => {
  assert.equal(buildDyDiscoveryPageUrl("search", "猫"), "https://www.douyin.com/?openbiliclaw_dy_task=1");
  assert.equal(buildDyDiscoveryPageUrl("hot", "2495363"), "https://www.douyin.com/?openbiliclaw_dy_task=1");
  assert.equal(buildDyDiscoveryPageUrl("feed"), "https://www.douyin.com/?openbiliclaw_dy_task=1");
});

test("search navigation resume only matches the requested Douyin result route", () => {
  assert.equal(
    isDySearchResultUrl(
      "https://www.douyin.com/jingxuan/search/%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD?type=video",
      "人工智能",
    ),
    true,
  );
  assert.equal(
    isDySearchResultUrl("https://www.douyin.com/search?keyword=%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD", "人工智能"),
    true,
  );
  assert.equal(
    isDySearchResultUrl("https://www.douyin.com/jingxuan/search/%E7%BE%8E%E9%A3%9F", "人工智能"),
    false,
  );
  assert.equal(
    isDySearchResultUrl(
      "https://www.douyin.com/jingxuan/search/%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD",
      "人工",
    ),
    false,
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

test("computeDyTaskTimeoutMs gives search enough time for DOM-triggered loading", () => {
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

test("computeDyTaskTimeoutMs gives feed enough time for passive response harvest", () => {
  const timeout = computeDyTaskTimeoutMs({ id: "feed-timeout", type: "feed", max_items: 10 });

  assert.ok(timeout >= 120_000, `expected retry-aware 120s feed budget, got ${timeout}`);
  assert.ok(timeout <= 360_000, `expected <= 360s ceiling, got ${timeout}`);
});

test("feed capture miss retries exactly once and no other failure is reloadable", () => {
  const captureMiss = {
    status: "failed" as const,
    error: "feed_no_observed_response",
  };
  assert.equal(shouldRetryDyFeedCapture(captureMiss, 0), true);
  assert.equal(shouldRetryDyFeedCapture(captureMiss, 1), false);
  assert.equal(
    shouldRetryDyFeedCapture({ status: "failed", error: "api_rate_limited" }, 0),
    false,
  );
  assert.equal(shouldRetryDyFeedCapture({ status: "empty" }, 0), false);
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
    hot_items: [
      {
        word: "热点词",
        sentence_id: "2495363",
        seed_aweme_id: "7652229189183427849",
      },
    ],
    max_items_per_hot: 8,
    max_items: 3,
  });

  assert.equal(data.task_id, "hot-task");
  assert.equal(data.type, "hot");
  assert.deepEqual(data.hot_items, [
    {
      word: "热点词",
      sentence_id: "2495363",
      seed_aweme_id: "7652229189183427849",
    },
  ]);
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

test("failed hot result maps to an immediate terminal failure", () => {
  assert.deepEqual(
    buildDyHotTerminalFailure({
      task_id: "hot-task",
      sentence_id: "2495363",
      word: "热点词",
      items: [],
      scope_count: 0,
      status: "failed",
      error: "fetch_tap_injection_failed",
      debug: { inject_status: "error: missing asset" },
    }),
    {
      task_id: "hot-task",
      status: "failed",
      error: "fetch_tap_injection_failed",
      debug: { inject_status: "error: missing asset" },
    },
  );
  assert.equal(
    buildDyHotTerminalFailure({
      task_id: "hot-task",
      sentence_id: "2495363",
      word: "热点词",
      items: [],
      scope_count: 0,
      status: "empty",
    }),
    null,
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

test("task-result delivery retries transient and non-2xx failures with the same body", async () => {
  const result = {
    task_id: "result-retry",
    status: "failed" as const,
    error: "fetch_tap_injection_failed",
  };
  const responses: Array<{ ok: boolean; status: number } | Error> = [
    { ok: false, status: 503 },
    new Error("backend restarting"),
    { ok: true, status: 200 },
  ];
  const requests: Array<{ input: string; init: RequestInit }> = [];
  const delays: number[] = [];

  await postDyTaskResult(
    result,
    {
      resolveUrl: async (path) => `http://127.0.0.1:8420/api${path}`,
      fetch: async (input, init) => {
        requests.push({ input, init });
        const response = responses.shift();
        if (response instanceof Error) throw response;
        assert.ok(response);
        return response;
      },
      sleep: async (delayMs) => { delays.push(delayMs); },
    },
    { maxAttempts: 3, baseDelayMs: 10 },
  );

  assert.equal(requests.length, 3);
  assert.deepEqual(delays, [10, 20]);
  assert.ok(requests.every(({ input }) => input.endsWith("/api/sources/dy/task-result")));
  assert.ok(requests.every(({ init }) => init.body === JSON.stringify(result)));
});

test("task-result delivery rejects after its bounded retry window", async () => {
  let fetchCalls = 0;
  const delays: number[] = [];

  await assert.rejects(
    postDyTaskResult(
      { task_id: "result-unacked", status: "failed", error: "task_timeout" },
      {
        resolveUrl: async () => "http://127.0.0.1:8420/api/sources/dy/task-result",
        fetch: async () => {
          fetchCalls += 1;
          return { ok: false, status: 503 };
        },
        sleep: async (delayMs) => { delays.push(delayMs); },
      },
      { baseDelayMs: 0 },
    ),
    /dy_task_result_unacknowledged: HTTP 503/,
  );

  assert.equal(fetchCalls, 3);
  assert.deepEqual(delays, [0, 0]);
});

test("poll takes the cross-source mutex before claiming a backend task", async () => {
  const shared = globalThis as DispatcherMutexGlobal;
  clearDispatcherMutex();
  shared.__OBC_DISPATCHER_MUTEX_HOLDER__ = "xhs";
  shared.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = Date.now();
  let fetchCalls = 0;
  try {
    await pollDyTaskOnce({
      ensureRecovery: async () => {},
      fetchTask: async () => {
        fetchCalls += 1;
        return null;
      },
      execute: async () => "accepted",
      reportDeclined: async () => {},
    });
    assert.equal(fetchCalls, 0);
  } finally {
    clearDispatcherMutex();
  }
});

test("poll does not claim when the runtime has no tabs execution capability", async () => {
  clearDispatcherMutex();
  let fetchCalls = 0;

  await pollDyTaskOnce({
    ensureRecovery: async () => {},
    canExecute: () => false,
    fetchTask: async () => {
      fetchCalls += 1;
      return null;
    },
    execute: async () => "accepted",
    reportDeclined: async () => {},
  });

  assert.equal(fetchCalls, 0);
  clearDispatcherMutex();
});

test("concurrent Douyin polls share one claim lifecycle", async () => {
  clearDispatcherMutex();
  let releaseRecovery!: () => void;
  const recoveryGate = new Promise<void>((resolve) => {
    releaseRecovery = resolve;
  });
  let recoveryCalls = 0;
  let fetchCalls = 0;
  const dependencies = {
    ensureRecovery: async () => {
      recoveryCalls += 1;
      await recoveryGate;
    },
    fetchTask: async () => {
      fetchCalls += 1;
      return null;
    },
    execute: async () => "accepted" as const,
    reportDeclined: async () => {},
  };

  const first = pollDyTaskOnce(dependencies);
  const second = pollDyTaskOnce(dependencies);
  assert.equal(first, second);
  releaseRecovery();
  await Promise.all([first, second]);

  assert.equal(recoveryCalls, 1);
  assert.equal(fetchCalls, 1);
  clearDispatcherMutex();
});

test("native-save releases the discovery mutex before durable execution", async () => {
  clearDispatcherMutex();
  const calls: Array<{ task: NativeSaveTask; mutexAlreadyHeld: boolean; holder?: string }> = [];
  await pollDyTaskOnce({
    ensureRecovery: async () => {},
    fetchTask: async () => nativeTask,
    execute: async (task, mutexAlreadyHeld) => {
      const shared = globalThis as DispatcherMutexGlobal;
      calls.push({
        task: task as NativeSaveTask,
        mutexAlreadyHeld,
        holder: shared.__OBC_DISPATCHER_MUTEX_HOLDER__,
      });
      return "accepted";
    },
    reportDeclined: async () => {},
  });

  assert.deepEqual(calls, [{ task: nativeTask, mutexAlreadyHeld: false, holder: undefined }]);
  clearDispatcherMutex();
});

test("a declined executor reports the claimed task and releases the mutex", async () => {
  clearDispatcherMutex();
  const task = { id: "declined-feed", type: "feed" as const, max_items: 3 };
  const declined: string[] = [];

  await pollDyTaskOnce({
    ensureRecovery: async () => {},
    fetchTask: async () => task,
    execute: async () => "declined",
    reportDeclined: async (claimed) => { declined.push(claimed.id); },
  });

  const shared = globalThis as DispatcherMutexGlobal;
  assert.deepEqual(declined, [task.id]);
  assert.equal(shared.__OBC_DISPATCHER_MUTEX_HOLDER__, undefined);
  clearDispatcherMutex();
});

test("pollDyTaskNow exists as the WS-driven immediate-poll entry point", () => {
  // Service-worker.ts calls this from runtimeSocket.onmessage when
  // backend broadcasts `dy_task_available`. We can't exercise the
  // chrome.tabs lifecycle here (no chrome global, no fetch backend),
  // but we MUST guarantee the export shape so the wire stays intact.
  assert.equal(typeof pollDyTaskOnce, "function");
  assert.equal(typeof pollDyTaskNow, "function");
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
