/**
 * Tests for the xhs task dispatcher.
 *
 * Pure helpers (buildTaskUrl, isValidTask) are tested directly. The
 * executeTask handshake is exercised with a hand-rolled chrome mock that
 * captures listeners and records outbound messages — no jsdom needed.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildTaskUrl,
  computeTaskTimeoutMs,
  executeTask,
  handleTaskResult,
  isValidTask,
  pollXhsTaskOnce,
  postXhsNativeSaveResult,
  type XhsTask,
} from "../src/background/xhs-task-dispatcher.ts";
import type { NativeSaveResult, NativeSaveTask } from "../src/shared/native-save.ts";

const nativeTask: NativeSaveTask = {
  id: "123e4567-e89b-42d3-a456-426614174011",
  type: "native_save",
  platform: "xiaohongshu",
  platform_slug: "xhs",
  item_key: "xiaohongshu:66aabbcc000000001e00dead",
  content_id: "66aabbcc000000001e00dead",
  content_url: "https://www.xiaohongshu.com/explore/66aabbcc000000001e00dead",
  content_type: "note",
  requested_action: "favorite",
  resolved_action: "favorite",
  target_label: "小红书收藏",
};

test("xhs task native_save union and dispatcher close through the exact authenticated result contract", async () => {
  assert.equal(isValidTask(nativeTask), true);
  assert.equal(isValidTask({ ...nativeTask, platform: "douyin", platform_slug: "dy" }), false);
  assert.equal(buildTaskUrl(nativeTask), nativeTask.content_url);
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
  assert.deepEqual(calls, [[nativeTask, "xhs"], result]);

  const requests: unknown[] = [];
  await postXhsNativeSaveResult(result, {
    resolveUrl: async (path) => `http://127.0.0.1:8420/api${path}`,
    fetch: async (input, init) => { requests.push([input, init]); },
  });
  assert.deepEqual(requests, [[
    "http://127.0.0.1:8420/api/sources/xhs/task-result",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(result),
    },
  ]]);
});

test("buildTaskUrl encodes keyword search URL", () => {
  const task: XhsTask = { id: "t1", type: "search", keyword: "机械键盘" };
  const url = buildTaskUrl(task);
  assert.equal(
    url,
    "https://www.xiaohongshu.com/search_result?keyword=%E6%9C%BA%E6%A2%B0%E9%94%AE%E7%9B%98&openbiliclaw_xhs_task=1",
  );
});

test("buildTaskUrl returns creator URL directly", () => {
  const task: XhsTask = {
    id: "t2",
    type: "creator",
    creator_url: "https://www.xiaohongshu.com/user/profile/abc",
  };
  assert.equal(
    buildTaskUrl(task),
    "https://www.xiaohongshu.com/user/profile/abc?openbiliclaw_xhs_task=1",
  );
});

test("buildTaskUrl routes bootstrap profile tasks to explore", () => {
  const task: XhsTask = { id: "t-bootstrap", type: "bootstrap_profile" };
  assert.equal(buildTaskUrl(task), "https://www.xiaohongshu.com/explore?openbiliclaw_xhs_task=1");
});

test("buildTaskUrl returns null for search without keyword", () => {
  const task: XhsTask = { id: "t3", type: "search" };
  assert.equal(buildTaskUrl(task), null);
});

test("buildTaskUrl returns null for creator without url", () => {
  const task: XhsTask = { id: "t4", type: "creator" };
  assert.equal(buildTaskUrl(task), null);
});

test("computeTaskTimeoutMs keeps normal tasks short and scales bootstrap scrolling", () => {
  assert.equal(
    computeTaskTimeoutMs({ id: "t-search", type: "search", keyword: "x" }),
    30_000,
  );
  assert.equal(
    computeTaskTimeoutMs({ id: "t-bootstrap", type: "bootstrap_profile" }),
    30_000,
  );
  assert.equal(
    computeTaskTimeoutMs({
      id: "t-bootstrap-scroll",
      type: "bootstrap_profile",
      max_scroll_rounds: 30,
    }),
    180_000,
  );
  assert.equal(
    computeTaskTimeoutMs({
      id: "t-bootstrap-scroll-single-scope",
      type: "bootstrap_profile",
      scopes: ["saved"],
      max_scroll_rounds: 30,
    }),
    120_000,
  );
  assert.equal(
    computeTaskTimeoutMs({
      id: "t-bootstrap-scroll-capped",
      type: "bootstrap_profile",
      max_scroll_rounds: 100,
    }),
    180_000,
  );
  assert.equal(
    computeTaskTimeoutMs({
      id: "t-bootstrap-scroll-wait",
      type: "bootstrap_profile",
      max_scroll_rounds: 30,
      scroll_wait_ms: 5_000,
    }),
    360_000,
  );
});

test("isValidTask accepts well-formed tasks", () => {
  assert.equal(isValidTask({ id: "t1", type: "search", keyword: "x" }), true);
  assert.equal(
    isValidTask({
      id: "t2",
      type: "creator",
      creator_url: "https://example.com",
    }),
    true,
  );
  assert.equal(
    isValidTask({
      id: "t3",
      type: "bootstrap_profile",
      scopes: ["saved", "liked", "xhs_history"],
      max_items_per_scope: 20,
      max_scroll_rounds: 0,
      scroll_wait_ms: 2_500,
      max_stagnant_scroll_rounds: 8,
    }),
    true,
  );
});

test("isValidTask rejects malformed input", () => {
  assert.equal(isValidTask(null), false);
  assert.equal(isValidTask({}), false);
  assert.equal(isValidTask({ id: "", type: "search" }), false);
  assert.equal(isValidTask({ id: "t1", type: "unknown" }), false);
  assert.equal(isValidTask("string"), false);
});

// ---------------------------------------------------------------------------
// executeTask handshake — regression test for the "all tasks time out" bug.
// ---------------------------------------------------------------------------

interface TabUpdatedListener {
  (tabId: number, changeInfo: { status?: string }): void;
}

interface ChromeMock {
  tabs: {
    create: (opts: { url: string; active?: boolean }) => Promise<{ id: number }>;
    query: (opts: { active?: boolean; currentWindow?: boolean }) => Promise<Array<{ id?: number; url?: string; status?: string }>>;
    update: (tabId: number, opts: { url?: string; active?: boolean; muted?: boolean }) => Promise<void>;
    remove: (tabId: number) => Promise<void>;
    sendMessage: (tabId: number, message: unknown) => Promise<void>;
    onUpdated: {
      addListener: (l: TabUpdatedListener) => void;
      removeListener: (l: TabUpdatedListener) => void;
      _listeners: TabUpdatedListener[];
      _emit: (tabId: number, changeInfo: { status?: string }) => void;
    };
  };
  alarms: { create: () => void };
}

interface MockState {
  createdTabs: { url: string; active?: boolean }[];
  sentMessages: { tabId: number; message: unknown }[];
  sendMessageImpl: (tabId: number, message: unknown) => Promise<void>;
  fetchCalls: { url: string; body?: unknown }[];
  updatedTabs: { tabId: number; url?: string; active?: boolean; muted?: boolean }[];
  removedTabs: number[];
  queriedTabs: { active?: boolean; currentWindow?: boolean }[];
  queryResult: Array<{ id?: number; url?: string; status?: string }>;
}

function installChromeMock(): MockState {
  const state: MockState = {
    createdTabs: [],
    sentMessages: [],
    sendMessageImpl: async () => {},
    fetchCalls: [],
    updatedTabs: [],
    removedTabs: [],
    queriedTabs: [],
    queryResult: [],
  };

  const listeners: TabUpdatedListener[] = [];
  const chromeMock: ChromeMock = {
    tabs: {
      create: async ({ url, active }) => {
        state.createdTabs.push({ url, active });
        return { id: 42 };
      },
      query: async (opts) => {
        state.queriedTabs.push(opts);
        return state.queryResult;
      },
      update: async (tabId, opts) => {
        state.updatedTabs.push({ tabId, ...opts });
      },
      remove: async (tabId) => {
        state.removedTabs.push(tabId);
      },
      sendMessage: (tabId, message) => {
        state.sentMessages.push({ tabId, message });
        return state.sendMessageImpl(tabId, message);
      },
      onUpdated: {
        _listeners: listeners,
        addListener: (l) => {
          listeners.push(l);
        },
        removeListener: (l) => {
          const i = listeners.indexOf(l);
          if (i >= 0) listeners.splice(i, 1);
        },
        _emit: (tabId, changeInfo) => {
          for (const l of [...listeners]) l(tabId, changeInfo);
        },
      },
    },
    alarms: { create: () => {} },
  };

  (globalThis as unknown as { chrome: ChromeMock }).chrome = chromeMock;
  (globalThis as unknown as { fetch: typeof fetch }).fetch = (async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ) => {
    state.fetchCalls.push({
      url: String(input),
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    return new Response(null, { status: 204 });
  }) as typeof fetch;

  return state;
}

// A tiny helper that flushes pending microtasks/macrotasks so Promise chains
// triggered inside listener callbacks can settle before we assert.
async function flush(): Promise<void> {
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
}

test("concurrent XHS poll triggers share one task claim", async () => {
  const state = installChromeMock();

  await Promise.all([pollXhsTaskOnce(), pollXhsTaskOnce()]);

  assert.equal(
    state.fetchCalls.filter((call) => call.url.endsWith("/sources/xhs/next-task")).length,
    1,
  );
});

test("XHS poll does not claim a backend task while another source holds the mutex", async () => {
  const state = installChromeMock();
  const mutexState = globalThis as unknown as {
    __OBC_DISPATCHER_MUTEX_HOLDER__?: string;
    __OBC_DISPATCHER_MUTEX_HELD_SINCE__?: number;
  };
  mutexState.__OBC_DISPATCHER_MUTEX_HOLDER__ = "dy";
  mutexState.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = Date.now();

  try {
    await pollXhsTaskOnce();
    assert.equal(
      state.fetchCalls.filter((call) => call.url.endsWith("/sources/xhs/next-task")).length,
      0,
      "the backend claim must happen only after the cross-source mutex is acquired",
    );
  } finally {
    mutexState.__OBC_DISPATCHER_MUTEX_HOLDER__ = undefined;
    mutexState.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = undefined;
  }
});

test("executeTask sends XHS_TASK_EXECUTE once the tab finishes loading", async () => {
  const state = installChromeMock();
  const chrome = (globalThis as unknown as { chrome: ChromeMock }).chrome;

  const task: XhsTask = { id: "t-handshake", type: "search", keyword: "手冲咖啡" };
  await executeTask(task);

  assert.equal(state.createdTabs.length, 1);
  assert.equal(state.sentMessages.length, 0, "no message before the tab is complete");
  assert.equal(chrome.tabs.onUpdated._listeners.length, 1, "listener registered");

  // Intermediate update — should be ignored.
  chrome.tabs.onUpdated._emit(42, { status: "loading" });
  await flush();
  assert.equal(state.sentMessages.length, 0);

  // Wrong tab id — should be ignored.
  chrome.tabs.onUpdated._emit(99, { status: "complete" });
  await flush();
  assert.equal(state.sentMessages.length, 0);

  // Now the page finishes loading — handshake fires once.
  chrome.tabs.onUpdated._emit(42, { status: "complete" });
  await flush();

  assert.equal(state.sentMessages.length, 1);
  assert.deepEqual(state.sentMessages[0], {
    tabId: 42,
    message: {
      action: "XHS_TASK_EXECUTE",
      data: { task_id: "t-handshake", type: "search" },
    },
  });
  assert.equal(chrome.tabs.onUpdated._listeners.length, 0, "listener detached after firing");

  // Subsequent completes (e.g. SPA re-navigations) must not resend.
  chrome.tabs.onUpdated._emit(42, { status: "complete" });
  await flush();
  assert.equal(state.sentMessages.length, 1);

  // Simulate the content script reporting back so module-level state resets.
  await handleTaskResult({
    task_id: "t-handshake",
    urls: ["https://example.com/explore/1"],
    status: "ok",
  });
  await flush();
});

test("executeTask opens bootstrap_profile in a foreground tab regardless of scroll rounds", async () => {
  const state = installChromeMock();

  // No max_scroll_rounds — earlier behaviour put this in the
  // background tab, but init-time bootstrap should always be visible.
  const task: XhsTask = { id: "t-bootstrap-no-scroll", type: "bootstrap_profile" };
  await executeTask(task);

  assert.deepEqual(state.createdTabs, [
    { url: "https://www.xiaohongshu.com/explore?openbiliclaw_xhs_task=1", active: true },
  ]);

  await handleTaskResult({ task_id: "t-bootstrap-no-scroll", urls: [], status: "ok" });
  await flush();
});

test("executeTask keeps search discovery in a background tab", async () => {
  const state = installChromeMock();

  const task: XhsTask = { id: "t-search-bg", type: "search", keyword: "demo" };
  await executeTask(task);

  assert.deepEqual(state.createdTabs, [
    {
      url: "https://www.xiaohongshu.com/search_result?keyword=demo&openbiliclaw_xhs_task=1",
      active: false,
    },
  ]);
  assert.deepEqual(state.queriedTabs, []);

  await handleTaskResult({ task_id: "t-search-bg", urls: [], status: "ok" });
  await flush();
  assert.deepEqual(state.removedTabs, [42]);
  assert.deepEqual(state.updatedTabs, [{ tabId: 42, muted: true }]);
});

test("rate-limited task result is reported and closes the task tab", async () => {
  const state = installChromeMock();
  const task: XhsTask = {
    id: "t-search-rate-limited",
    type: "search",
    keyword: "demo",
  };
  await executeTask(task);

  await handleTaskResult({
    task_id: task.id,
    urls: [],
    notes: [],
    status: "rate_limited",
    error: "xhs_rate_limited",
    debug: {
      xhs_risk_control: {
        reason: "security_verification",
      },
    },
  });
  await flush();

  const resultPost = state.fetchCalls.find((call) =>
    call.url.endsWith("/sources/xhs/task-result")
  );
  assert.ok(resultPost);
  assert.deepEqual(resultPost.body, {
    task_id: task.id,
    urls: [],
    notes: [],
    status: "rate_limited",
    error: "xhs_rate_limited",
    debug: {
      xhs_risk_control: {
        reason: "security_verification",
      },
    },
  });
  assert.deepEqual(state.removedTabs, [42]);
});

test("executeTask opens explore active and waits after clicked profile navigation", async () => {
  const state = installChromeMock();
  const chrome = (globalThis as unknown as { chrome: ChromeMock }).chrome;

  const task: XhsTask = {
    id: "t-bootstrap-active",
    type: "bootstrap_profile",
    max_scroll_rounds: 30,
  };
  await executeTask(task);

  assert.deepEqual(state.createdTabs, [
    {
      url: "https://www.xiaohongshu.com/explore?openbiliclaw_xhs_task=1",
      active: true,
    },
  ]);
  assert.deepEqual(state.queriedTabs, []);

  chrome.tabs.onUpdated._emit(42, { status: "complete" });
  await flush();
  assert.equal(state.sentMessages.length, 1);

  await handleTaskResult({
    task_id: "t-bootstrap-active",
    urls: [],
    notes: [],
    status: "empty",
    next_url: "https://www.xiaohongshu.com/user/profile/current-user",
    debug: {
      xhs_bootstrap: {
        steps: [
          {
            page_url: "https://www.xiaohongshu.com/explore",
            next_url_clicked: true,
          },
        ],
      },
    },
  });
  await flush();

  assert.deepEqual(
    state.updatedTabs,
    [{ tabId: 42, muted: true }],
    "clicked profile navigation must not call tabs.update beyond the initial mute",
  );

  chrome.tabs.onUpdated._emit(42, { status: "complete" });
  await flush();
  assert.equal(state.sentMessages.length, 2);

  await handleTaskResult({
    task_id: "t-bootstrap-active",
    urls: [],
    status: "empty",
  });
  await flush();
  assert.deepEqual(state.removedTabs, [42]);
});

test("executeTask reports sendMessage_failed when content script is absent", async () => {
  const state = installChromeMock();
  const chrome = (globalThis as unknown as { chrome: ChromeMock }).chrome;
  state.sendMessageImpl = async () => {
    throw new Error("no receiving end");
  };

  const task: XhsTask = { id: "t-no-receiver", type: "search", keyword: "x" };
  await executeTask(task);

  chrome.tabs.onUpdated._emit(42, { status: "complete" });
  await flush();

  const resultPost = state.fetchCalls.find(
    (c) => c.url.endsWith("/task-result") && (c.body as { task_id: string }).task_id === "t-no-receiver",
  );
  assert.ok(resultPost, "expected a task-result POST");
  assert.deepEqual(resultPost!.body, {
    task_id: "t-no-receiver",
    urls: [],
    status: "error",
    error: "sendMessage_failed",
  });
});

test("bootstrap task follows a discovered profile URL before reporting the result", async () => {
  const state = installChromeMock();
  const chrome = (globalThis as unknown as { chrome: ChromeMock }).chrome;
  const task: XhsTask = {
    id: "t-bootstrap-nav",
    type: "bootstrap_profile",
    scopes: ["saved", "liked", "xhs_history"],
    scroll_wait_ms: 2_500,
    max_stagnant_scroll_rounds: 8,
  };

  await executeTask(task);
  chrome.tabs.onUpdated._emit(42, { status: "complete" });
  await flush();

  assert.equal(state.sentMessages.length, 1);
  await handleTaskResult({
    task_id: "t-bootstrap-nav",
    urls: [],
    notes: [],
    scope_counts: { saved: 0, liked: 0, xhs_history: 0 },
    status: "empty",
    next_url: "https://www.xiaohongshu.com/user/profile/current-user",
    debug: {
      xhs_bootstrap: {
        steps: [
          {
            page_url: "https://www.xiaohongshu.com/explore",
            profile_url_found: true,
            profile_url_source: "document",
          },
        ],
      },
    },
  });
  await flush();

  assert.deepEqual(state.updatedTabs, [
    { tabId: 42, muted: true },
    {
      tabId: 42,
      url: "https://www.xiaohongshu.com/user/profile/current-user?openbiliclaw_xhs_task=1",
    },
  ]);
  assert.equal(
    state.fetchCalls.filter((c) => c.url.endsWith("/task-result")).length,
    0,
    "intermediate navigation result must not be posted to the backend",
  );

  chrome.tabs.onUpdated._emit(42, { status: "complete" });
  await flush();

  assert.equal(state.sentMessages.length, 2);
  assert.deepEqual(state.sentMessages[1], {
    tabId: 42,
    message: {
      action: "XHS_TASK_EXECUTE",
      data: {
        task_id: "t-bootstrap-nav",
        type: "bootstrap_profile",
        scopes: ["saved", "liked", "xhs_history"],
        scroll_wait_ms: 2_500,
        max_stagnant_scroll_rounds: 8,
      },
    },
  });

  await handleTaskResult({
    task_id: "t-bootstrap-nav",
    urls: ["https://www.xiaohongshu.com/explore/saved-id"],
    status: "ok",
    debug: {
      xhs_bootstrap: {
        steps: [
          {
            page_url: "https://www.xiaohongshu.com/user/profile/current-user",
            has_initial_state: true,
            state_counts: { saved: 1, liked: 0, xhs_history: 0 },
          },
        ],
      },
    },
  });
  await flush();

  const finalPost = state.fetchCalls.find((c) => c.url.endsWith("/task-result"));
  assert.ok(finalPost, "expected final task-result POST");
  assert.deepEqual(finalPost!.body, {
    task_id: "t-bootstrap-nav",
    urls: ["https://www.xiaohongshu.com/explore/saved-id"],
    status: "ok",
    debug: {
      xhs_bootstrap: {
        steps: [
          {
            page_url: "https://www.xiaohongshu.com/explore",
            profile_url_found: true,
            profile_url_source: "document",
          },
          {
            page_url: "https://www.xiaohongshu.com/user/profile/current-user",
            has_initial_state: true,
            state_counts: { saved: 1, liked: 0, xhs_history: 0 },
          },
        ],
      },
    },
  });
});

test("bootstrap partial results are posted without closing the task", async () => {
  const state = installChromeMock();
  const chrome = (globalThis as unknown as { chrome: ChromeMock }).chrome;
  const task: XhsTask = {
    id: "t-bootstrap-partial",
    type: "bootstrap_profile",
    scopes: ["saved", "liked"],
  };

  await executeTask(task);
  chrome.tabs.onUpdated._emit(42, { status: "complete" });
  await flush();

  await handleTaskResult({
    task_id: "t-bootstrap-partial",
    urls: ["https://www.xiaohongshu.com/explore/partial"],
    notes: [{ scope: "saved", note_id: "partial" }],
    scope_counts: { saved: 1, liked: 0 },
    status: "partial",
  });
  await flush();

  const partialPost = state.fetchCalls.find((c) => c.url.endsWith("/task-result"));
  assert.ok(partialPost, "expected partial task-result POST");
  assert.deepEqual(partialPost!.body, {
    task_id: "t-bootstrap-partial",
    urls: ["https://www.xiaohongshu.com/explore/partial"],
    notes: [{ scope: "saved", note_id: "partial" }],
    scope_counts: { saved: 1, liked: 0 },
    status: "partial",
  });

  chrome.tabs.onUpdated._emit(42, { status: "complete" });
  await flush();
  assert.equal(
    state.sentMessages.length,
    1,
    "partial result must keep task in flight without re-handshaking",
  );

  await handleTaskResult({
    task_id: "t-bootstrap-partial",
    urls: ["https://www.xiaohongshu.com/explore/final"],
    status: "ok",
  });
  await flush();

  assert.equal(
    state.fetchCalls.filter((c) => c.url.endsWith("/task-result")).length,
    2,
  );
});
