import test from "node:test";
import assert from "node:assert/strict";

import {
  normalizeDelightCandidate,
  normalizeRecommendation,
} from "../popup/popup-helpers.js";
import {
  captureSavedFocus as capturePopupSavedFocus,
  createRetainedSavedListState as createPopupRetainedState,
  createSavedSyncTaskTracker,
  normalizeCanonicalSavedItem,
  partitionSavedQueueResults,
  restoreSavedFocus as restorePopupSavedFocus,
} from "../popup/popup-saved-sync.js";
import {
  fetchConfig as fetchPopupConfig,
  fetchSavedItems as fetchPopupSavedItems,
  normalizeSavedItemInput,
  pollSavedSyncTask as pollPopupSavedSyncTask,
  removeSavedItem as removePopupSavedItem,
  saveItem as savePopupItem,
  savedItemStatus as popupSavedItemStatus,
  syncSavedItems as syncPopupSavedItems,
  updateConfig as updatePopupConfig,
} from "../popup/popup-api.js";
import { __resetBackendEndpointForTests } from "../popup/popup-backend-config.js";
import { __resetPopupDeviceAuthForTests } from "../popup/popup-device-auth.js";
import {
  createDurableTaskTracker as createMobileTaskTracker,
  createRetainedSavedListState,
  createSavedMutationRegistry,
  captureSavedFocus,
  createDialogFocusController,
  restoreSavedFocus,
} from "../../src/openbiliclaw/web/js/saved-sync-runtime.js";
import {
  normalizeDelightCandidate as normalizeMobileDelight,
  normalizeRecommendation as normalizeMobileRecommendation,
  normalizeSavedIdentity as normalizeMobileSavedIdentity,
} from "../../src/openbiliclaw/web/js/view-models.js";

const identities = [
  {
    item_key: "youtube:yt-1",
    source_platform: "youtube",
    content_id: "yt-1",
    content_url: "https://www.youtube.com/watch?v=yt-1",
    content_type: "video",
  },
  {
    item_key: "twitter:1900000000000000001",
    source_platform: "twitter",
    content_id: "1900000000000000001",
    content_url: "https://x.com/openai/status/1900000000000000001",
    content_type: "tweet",
  },
  {
    item_key: "zhihu:answer-42",
    source_platform: "zhihu",
    content_id: "answer-42",
    content_url: "https://www.zhihu.com/question/1/answer/42",
    content_type: "answer",
  },
  {
    item_key: "web:url:0123456789abcdef01234567",
    source_platform: "web",
    content_id: "",
    content_url: "https://example.com/articles/local-first",
    content_type: "article",
  },
];

test("recommendation and delight normalizers preserve canonical saved identity", () => {
  for (const identity of identities) {
    const raw = { ...identity, id: 99, bvid: "", title: "demo" };
    for (const normalized of [
      normalizeRecommendation(raw),
      normalizeDelightCandidate(raw),
      normalizeMobileRecommendation(raw),
      normalizeMobileDelight(raw),
    ]) {
      assert.deepEqual(
        {
          item_key: normalized.item_key,
          source_platform: normalized.source_platform,
          content_id: normalized.content_id,
          content_url: normalized.content_url,
          content_type: normalized.content_type,
        },
        identity,
      );
    }
  }

  const namespacedText = {
    item_key: "twitter:1900000000000000001",
    source_platform: "twitter",
    bvid: "twitter:1900000000000000001",
    content_url: "https://x.com/openai/status/1900000000000000001",
  };
  for (const normalized of [
    normalizeRecommendation(namespacedText),
    normalizeMobileRecommendation(namespacedText),
  ]) {
    assert.equal(normalized.content_id, "");
    assert.equal(normalized.content_type, "");
  }
});

test("saved identity never uses a recommendation row id or namespaced legacy id as content_id", () => {
  assert.deepEqual(normalizeCanonicalSavedItem({
    id: 77,
    bvid: "youtube:yt-1",
    item_key: "youtube:yt-1",
    source_platform: "youtube",
    content_url: "https://www.youtube.com/watch?v=yt-1",
    content_type: "video",
  }), {
    item_key: "youtube:yt-1",
    source_platform: "youtube",
    content_id: "",
    content_url: "https://www.youtube.com/watch?v=yt-1",
    content_type: "video",
  });

  assert.equal(normalizeCanonicalSavedItem({
    id: 88,
    item_key: "web:url:0123456789abcdef01234567",
    source_platform: "web",
    content_url: "https://example.com/story",
    content_type: "article",
  }).content_id, "");

  assert.deepEqual(normalizeMobileSavedIdentity({
    id: 99,
    bvid: "twitter:1900000000000000001",
    item_key: "twitter:1900000000000000001",
    source_platform: "twitter",
    content_url: "https://x.com/openai/status/1900000000000000001",
    content_type: "tweet",
  }), {
    id: 99,
    bvid: "twitter:1900000000000000001",
    item_key: "twitter:1900000000000000001",
    source_platform: "twitter",
    content_id: "",
    content_url: "https://x.com/openai/status/1900000000000000001",
    content_type: "tweet",
  });
});

test("popup saved identity canonicalizes Weibo aliases and exact host boundaries", () => {
  for (const source_platform of ["weibo", "wb", "微博"]) {
    assert.equal(normalizeCanonicalSavedItem({
      source_platform,
      content_id: "5023456789012345",
    }).source_platform, "weibo");
  }
  for (const content_url of [
    "https://weibo.com/2803301701/RcExample",
    "https://m.weibo.cn/detail/5023456789012345",
    "https://wx1.sinaimg.cn/large/example.jpg",
  ]) {
    assert.equal(normalizeCanonicalSavedItem({
      content_url,
      content_id: "5023456789012345",
    }).source_platform, "weibo");
  }
  assert.equal(normalizeCanonicalSavedItem({
    content_url: "https://evilweibo.com/5023456789012345",
    content_id: "5023456789012345",
  }).source_platform, "web");
});

test("save payload normalization does not force unknown text content to video", () => {
  assert.equal(normalizeSavedItemInput({
    item_key: "twitter:url:0123456789abcdef01234567",
    source_platform: "twitter",
    content_id: "",
    content_url: "https://x.com/openai/status/1900000000000000001",
    content_type: "",
  }).content_type, "");
  assert.equal(normalizeSavedItemInput({
    id: 77,
    bvid: "youtube:yt-1",
    source_platform: "youtube",
    content_url: "https://www.youtube.com/watch?v=yt-1",
  }).content_id, "");
});

test("desktop saved API uses bounded strict requests and propagates failures", async () => {
  delete (globalThis as any).OpenBiliClawSavedSync;
  await import("../../src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js");
  const core = (globalThis as any).OpenBiliClawSavedSync;
  const calls: Array<{ path: string; options: Record<string, unknown> }> = [];
  const api = core.createStrictSavedApi(async (path: string, options = {}) => {
    calls.push({ path, options });
    if (path.includes("/status")) throw new Error("HTTP 503");
    return { ok: true };
  });

  const xItem = {
    id: 9,
    item_key: "twitter:1900000000000000001",
    source_platform: "twitter",
    content_id: "1900000000000000001",
    content_url: "https://x.com/openai/status/1900000000000000001",
    content_type: "tweet",
  };
  assert.deepEqual(core.normalizeSavedItem(xItem), xItem);
  assert.deepEqual(core.normalizeSavedItem({
    source_platform: "x",
    content_id: "1900000000000000002",
    content_url: "https://x.com/openai/status/1900000000000000002",
    content_type: "tweet",
  }), {
    source_platform: "twitter",
    content_id: "1900000000000000002",
    content_url: "https://x.com/openai/status/1900000000000000002",
    content_type: "tweet",
    item_key: "twitter:1900000000000000002",
  });
  assert.deepEqual(core.normalizeSavedItem({
    content_id: "326",
    content_url: "https://bangumi.tv/subject/326",
    content_type: "subject",
  }), {
    content_id: "326",
    content_url: "https://bangumi.tv/subject/326",
    content_type: "subject",
    item_key: "bangumi:326",
    source_platform: "bangumi",
  });
  assert.deepEqual(core.normalizeSavedItem({
    source_platform: "gh",
    content_id: "repository:101",
    content_url: "https://github.com/alice/local-agent",
    content_type: "repository",
  }), {
    source_platform: "github",
    content_id: "repository:101",
    content_url: "https://github.com/alice/local-agent",
    content_type: "repository",
    item_key: "github:repository:101",
  });
  assert.deepEqual(core.normalizeSavedItem({
    content_id: "repository:102",
    content_url: "https://github.com/alice/another-agent",
    content_type: "repository",
  }), {
    content_id: "repository:102",
    content_url: "https://github.com/alice/another-agent",
    content_type: "repository",
    item_key: "github:repository:102",
    source_platform: "github",
  });
  await api.save("favorite", xItem);
  await api.remove("favorite", xItem.item_key);
  await api.list("favorite");
  await api.sync("favorite", [xItem.item_key]);
  await api.pollTask("123e4567-e89b-12d3-a456-426614174000");
  await assert.rejects(api.status("favorite", xItem.item_key), /HTTP 503/);

  assert.equal(calls.length, 6);
  for (const call of calls) {
    assert.equal(typeof call.options.timeoutMs, "number");
    assert.ok(Number(call.options.timeoutMs) > 0);
    assert.ok(Number(call.options.timeoutMs) <= 15_000);
  }
});

test("durable task tracker keeps nonterminal tasks resumable beyond the foreground horizon", async () => {
  const core = (globalThis as any).OpenBiliClawSavedSync;
  let now = 0;
  let visible = true;
  const scheduled: Array<{ run: () => void; delay: number }> = [];
  const snapshots = [
    { task_id: "task-1", items: [{ item_key: "youtube:1", status: "syncing" }] },
    { task_id: "task-1", items: [{ item_key: "youtube:1", status: "syncing" }] },
    { task_id: "task-1", items: [{ item_key: "youtube:1", status: "synced" }] },
  ];
  const events: string[] = [];
  const tracker = core.createDurableTaskTracker({
    poll: async () => snapshots.shift(),
    now: () => now,
    isVisible: () => visible,
    schedule: (run: () => void, delay: number) => {
      scheduled.push({ run, delay });
      return scheduled.length;
    },
    cancel: () => {},
    foregroundHorizonMs: 20_000,
    visibleDelayMs: 500,
    hiddenDelayMs: 5_000,
  });

  tracker.track(snapshots.shift(), {
    onProgress: () => events.push("progress"),
    onBackground: () => events.push("仍在后台同步"),
    onTerminal: () => events.push("terminal"),
  });
  assert.equal(scheduled[0].delay, 500);
  now = 21_000;
  visible = false;
  await scheduled.shift().run();
  assert.ok(events.includes("仍在后台同步"));
  assert.equal(tracker.has("task-1"), true);
  assert.equal(scheduled[0].delay, 5_000);
  visible = true;
  await scheduled.shift().run();
  assert.ok(events.includes("terminal"));
  assert.equal(tracker.has("task-1"), false);
});

test("saved list state retains the last successful rows when refresh fails", () => {
  const state = createRetainedSavedListState();
  state.commit({ items: [{ item_key: "youtube:1" }], total: 1 });
  state.fail(new Error("offline"));
  assert.deepEqual(state.snapshot(), {
    items: [{ item_key: "youtube:1" }],
    total: 1,
    loaded: true,
    error: "offline",
  });
  state.commit({ items: [{ item_key: "twitter:2" }], total: 1 });
  assert.equal(state.snapshot().error, "");
  assert.equal(state.snapshot().items[0].item_key, "twitter:2");
});

test("saved mutation registry isolates keys and discards stale hydration", async () => {
  const registry = createSavedMutationRegistry();
  let finishHydration: (value: unknown) => void = () => {};
  const hydration = registry.hydrate("favorite", "youtube:1", () => new Promise((resolve) => {
    finishHydration = resolve;
  }));
  let finishSave: (value: unknown) => void = () => {};
  const mutation = registry.toggle("favorite", "youtube:1", {
    add: () => new Promise((resolve) => { finishSave = resolve; }),
    remove: async () => ({ saved: false }),
  });
  assert.equal(registry.isBusy("favorite", "youtube:1"), true);
  assert.equal(registry.isBusy("favorite", "twitter:2"), false);
  assert.equal(registry.isSaved("favorite", "youtube:1"), true);
  finishSave({ saved: true });
  await mutation;
  finishHydration({ saved: false });
  await hydration;
  assert.equal(registry.isSaved("favorite", "youtube:1"), true);
});

test("mobile task tracker survives an aborted poll and remains resumable", async () => {
  const scheduled: Array<() => void> = [];
  let attempts = 0;
  const tracker = createMobileTaskTracker({
    poll: async () => {
      attempts += 1;
      if (attempts === 1) throw Object.assign(new Error("aborted"), { name: "AbortError" });
      return { task_id: "task-mobile", items: [{ item_key: "youtube:1", status: "synced" }] };
    },
    schedule: (run: () => void) => { scheduled.push(run); return scheduled.length; },
    cancel: () => {},
  });
  tracker.track({
    task_id: "task-mobile",
    items: [{ item_key: "youtube:1", status: "syncing" }],
  });
  await scheduled.shift()();
  assert.equal(tracker.has("task-mobile"), true);
  assert.equal(tracker.resume("task-mobile"), true);
  await scheduled.pop()();
  assert.equal(tracker.has("task-mobile"), false);
});

test("saved focus token restores the same item action after rerender", () => {
  let focused = 0;
  const action = {
    dataset: { savedAction: "remove" },
    focus() { focused += 1; },
    closest(selector: string) { return selector === "[data-item-key]" ? card : null; },
  };
  const card = {
    dataset: { itemKey: "youtube:video-1" },
    querySelectorAll() { return [action]; },
  };
  const root = { querySelectorAll() { return [card]; } };
  const token = captureSavedFocus(root, action);
  assert.deepEqual(token, { itemKey: "youtube:video-1", action: "remove", index: 0 });
  assert.equal(restoreSavedFocus(root, token), true);
  assert.equal(focused, 1);
});

test("extension saved runtime retains list state and keeps polling after the horizon", async () => {
  const retained = createPopupRetainedState();
  retained.commit({ items: [{ item_key: "zhihu:1" }], total: 1 });
  retained.fail("offline");
  assert.equal(retained.snapshot().items[0].item_key, "zhihu:1");

  let now = 0;
  const scheduled: Array<() => void> = [];
  const messages: string[] = [];
  const tracker = createSavedSyncTaskTracker({
    poll: async () => ({ task_id: "popup-task", items: [{ item_key: "zhihu:1", status: "syncing" }] }),
    now: () => now,
    schedule: (run: () => void) => { scheduled.push(run); return scheduled.length; },
    cancel: () => {},
    foregroundHorizonMs: 20_000,
  });
  tracker.track({ task_id: "popup-task", items: [{ item_key: "zhihu:1", status: "syncing" }] }, {
    onBackground: () => messages.push("仍在后台同步"),
  });
  now = 21_000;
  await scheduled.shift()();
  assert.deepEqual(messages, ["仍在后台同步"]);
  assert.equal(tracker.has("popup-task"), true);

  const action = { dataset: { savedAction: "sync" }, focus() {}, closest: () => card };
  const card = { dataset: { itemKey: "zhihu:1" }, querySelectorAll: () => [action] };
  const root = { querySelectorAll: () => [card] };
  assert.equal(restorePopupSavedFocus(root, capturePopupSavedFocus(root, action)), true);
});

test("desktop saved core retains rows, isolates mutations, and restores focus", async () => {
  const core = (globalThis as any).OpenBiliClawSavedSync;
  const retained = core.createRetainedSavedListState();
  retained.commit({ items: [{ item_key: "reddit:t3_1" }], total: 1 });
  retained.fail("offline");
  assert.equal(retained.snapshot().items[0].item_key, "reddit:t3_1");

  const registry = core.createSavedMutationRegistry();
  const first = registry.toggle("favorite", "reddit:t3_1", {
    add: async () => ({ saved: true }),
    remove: async () => ({ saved: false }),
  });
  assert.equal(registry.isBusy("favorite", "reddit:t3_1"), true);
  assert.equal(registry.isBusy("favorite", "youtube:2"), false);
  await first;

  let focused = false;
  const action = { dataset: { savedAction: "sync" }, focus() { focused = true; }, closest: () => card };
  const card = { dataset: { itemKey: "reddit:t3_1" }, querySelectorAll: () => [action] };
  const root = { querySelectorAll: () => [card] };
  const token = core.captureSavedFocus(root, action);
  assert.equal(core.restoreSavedFocus(root, token), true);
  assert.equal(focused, true);
});

test("dialog focus controller closes on Escape and restores its opener", () => {
  const listeners = new Map<string, (event: any) => void>();
  let openerFocused = 0;
  let closed = 0;
  const opener = { focus() { openerFocused += 1; } };
  const first = { focus() {} };
  const last = { focus() {} };
  const doc = {
    activeElement: first,
    addEventListener(type: string, fn: (event: any) => void) { listeners.set(type, fn); },
    removeEventListener(type: string) { listeners.delete(type); },
  };
  const dialog = {
    contains: () => true,
    querySelectorAll: () => [first, last],
  };
  const controller = createDialogFocusController({
    dialog,
    opener,
    document: doc,
    onClose: () => { closed += 1; },
  });
  controller.activate();
  listeners.get("keydown")?.({ key: "Escape", preventDefault() {} });
  assert.equal(closed, 1);
  controller.deactivate();
  assert.equal(openerFocused, 1);
});

test("extension all-queue keeps failed URL items and accepts server-issued item keys", () => {
  const urlItem = {
    item_key: "",
    source_platform: "web",
    content_id: "",
    content_url: "https://example.com/story",
    content_type: "article",
  };
  const failed = { ...urlItem, content_url: "https://example.com/failed" };
  const partition = partitionSavedQueueResults([urlItem, failed], [
    { status: "fulfilled", value: { saved: true, item_key: "web:url:0123456789abcdef01234567" } },
    { status: "rejected", reason: new Error("offline") },
  ]);
  assert.equal(partition.savedCount, 1);
  assert.equal(partition.failedCount, 1);
  assert.equal(partition.saved[0].itemKey, "web:url:0123456789abcdef01234567");
  assert.deepEqual(partition.remaining, [failed]);
});

function installAbortAwareNeverFetch(safetyMs = 80) {
  const original = globalThis.fetch;
  const seenSignals: AbortSignal[] = [];
  globalThis.fetch = (async (_input: unknown, init: RequestInit = {}) => new Promise((_resolve, reject) => {
    const signal = init.signal as AbortSignal | undefined;
    if (signal) seenSignals.push(signal);
    const safety = setTimeout(() => reject(Object.assign(new Error("safety timeout"), { name: "SafetyError" })), safetyMs);
    const abort = () => {
      clearTimeout(safety);
      reject(signal?.reason || Object.assign(new Error("aborted"), { name: "AbortError" }));
    };
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
  })) as typeof fetch;
  return {
    seenSignals,
    restore() { globalThis.fetch = original; },
  };
}

function installPopupAuthStorage(initial: Record<string, unknown>) {
  const values = { ...initial };
  const originalChrome = (globalThis as any).chrome;
  (globalThis as any).chrome = { storage: { local: {
    get(keys: string | string[], callback: (items: Record<string, unknown>) => void) {
      const selected = Array.isArray(keys) ? keys : [keys];
      callback(Object.fromEntries(selected.filter((key) => key in values).map((key) => [key, values[key]])));
    },
    set(items: Record<string, unknown>, callback: () => void) {
      Object.assign(values, items);
      callback();
    },
    remove(keys: string | string[], callback: () => void) {
      for (const key of Array.isArray(keys) ? keys : [keys]) delete values[key];
      callback();
    },
  } } };
  __resetPopupDeviceAuthForTests();
  __resetBackendEndpointForTests();
  return {
    restore() {
      (globalThis as any).chrome = originalChrome;
      __resetPopupDeviceAuthForTests();
      __resetBackendEndpointForTests();
    },
  };
}

function neverSettlingAuthFetch(authSignals: AbortSignal[], protectedStatus = 200) {
  return (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    if (!String(input).endsWith("/auth/extension-token")) {
      return new Response("{}", { status: protectedStatus });
    }
    if (init.signal) authSignals.push(init.signal);
    return new Promise<Response>((_resolve, reject) => {
      const safety = setTimeout(() => reject(Object.assign(new Error("auth safety timeout"), {
        name: "SafetyError",
      })), 80);
      init.signal?.addEventListener("abort", () => {
        clearTimeout(safety);
        reject(init.signal?.reason || new DOMException("Aborted", "AbortError"));
      }, { once: true });
    });
  }) as typeof fetch;
}

test("popup saved deadline aborts a never-settling initial session exchange", async () => {
  const storage = installPopupAuthStorage({ obc_extension_device_key: "fresh-device-key" });
  const originalFetch = globalThis.fetch;
  const authSignals: AbortSignal[] = [];
  globalThis.fetch = neverSettlingAuthFetch(authSignals);
  try {
    await assert.rejects(fetchPopupSavedItems("favorite", 10, 0, 5), { name: "AbortError" });
    assert.equal(authSignals.length, 1);
    assert.equal(authSignals[0].aborted, true);
  } finally {
    globalThis.fetch = originalFetch;
    storage.restore();
  }
});

test("popup saved deadline aborts a never-settling forced refresh after 401", async () => {
  const storage = installPopupAuthStorage({
    obc_extension_device_key: "refresh-device-key",
    obc_auth_session: { token: "expired-by-server", expires_at: 2_000_000_000 },
  });
  const originalFetch = globalThis.fetch;
  const authSignals: AbortSignal[] = [];
  globalThis.fetch = neverSettlingAuthFetch(authSignals, 401);
  try {
    await assert.rejects(fetchPopupSavedItems("watch_later", 10, 0, 5), { name: "AbortError" });
    assert.equal(authSignals.length, 1);
    assert.equal(authSignals[0].aborted, true);
  } finally {
    globalThis.fetch = originalFetch;
    storage.restore();
  }
});

test("extension saved and config requests abort a never-resolving fetch within their supplied bound", async () => {
  const never = installAbortAwareNeverFetch();
  try {
    await assert.rejects(fetchPopupSavedItems("favorite", 10, 0, 5), { name: "AbortError" });
    await assert.rejects(popupSavedItemStatus("favorite", "youtube:1", 5), { name: "AbortError" });
    await assert.rejects(removePopupSavedItem("favorite", "youtube:1", 5), { name: "AbortError" });
    await assert.rejects(syncPopupSavedItems("favorite", ["youtube:1"], 5), { name: "AbortError" });
    await assert.rejects(pollPopupSavedSyncTask("task-timeout", 5), { name: "AbortError" });
    await assert.rejects(fetchPopupConfig(5), { name: "AbortError" });
    await assert.rejects(updatePopupConfig({ saved_sync: { auto_sync_enabled: false } }, 5), { name: "AbortError" });
    assert.equal(never.seenSignals.length, 7);
    assert.ok(never.seenSignals.every((signal) => signal.aborted));
  } finally {
    never.restore();
  }
});

test("a timed-out extension mutation clears per-item busy state", async () => {
  const never = installAbortAwareNeverFetch();
  const registry = createSavedMutationRegistry();
  try {
    const mutation = registry.toggle("favorite", "youtube:timeout", {
      add: () => savePopupItem("favorite", {
        source_platform: "youtube",
        content_id: "timeout",
        content_url: "https://youtube.com/watch?v=timeout",
        content_type: "video",
      }, 5),
      remove: async () => ({ saved: false }),
    });
    assert.equal(registry.isBusy("favorite", "youtube:timeout"), true);
    await assert.rejects(mutation, { name: "AbortError" });
    assert.equal(registry.isBusy("favorite", "youtube:timeout"), false);
  } finally {
    never.restore();
  }
});

test("mobile saved and config requests abort and remain retryable after a hung fetch", async () => {
  const oldLocation = (globalThis as any).location;
  (globalThis as any).location = { protocol: "http:", host: "127.0.0.1:8420" };
  const api = await import(`../../src/openbiliclaw/web/js/api.js?review-timeout=${Date.now()}`);
  const never = installAbortAwareNeverFetch();
  try {
    await assert.rejects(api.fetchSavedItems("watch_later", 10, 0, 5), { name: "AbortError" });
    await assert.rejects(api.savedItemStatus("watch_later", "youtube:1", 5), { name: "AbortError" });
    await assert.rejects(api.saveItem("watch_later", { source_platform: "youtube", content_id: "1" }, 5), { name: "AbortError" });
    await assert.rejects(api.removeSavedItem("watch_later", "youtube:1", 5), { name: "AbortError" });
    await assert.rejects(api.syncSavedItems("watch_later", ["youtube:1"], 5), { name: "AbortError" });
    await assert.rejects(api.pollSavedSyncTask("task-timeout", 5), { name: "AbortError" });
    await assert.rejects(api.fetchConfig(5), { name: "AbortError" });
    await assert.rejects(api.updateConfig({ saved_sync: { auto_sync_enabled: false } }, 5), { name: "AbortError" });
    assert.equal(never.seenSignals.length, 8);
    assert.ok(never.seenSignals.every((signal) => signal.aborted));
  } finally {
    never.restore();
    (globalThis as any).location = oldLocation;
  }
});

async function exerciseRecoveredTaskCoordinator(createCoordinator: Function) {
  const tracked = new Map<string, any>();
  const tracker = {
    has(taskId: string) { return tracked.has(taskId); },
    track(task: any, callbacks: any) { tracked.set(task.task_id, callbacks); return task.task_id; },
    stop(taskId: string) { return tracked.delete(taskId); },
  };
  let fetches = 0;
  let terminals = 0;
  const coordinator = createCoordinator({
    tracker,
    fetchTask: async (taskId: string) => {
      fetches += 1;
      return { task_id: taskId, items: [{ item_key: "youtube:1", status: "syncing" }] };
    },
    onTerminal: () => { terminals += 1; },
  });
  const rows = [
    { item_key: "youtube:1", sync_status: "syncing", sync_task_id: "persisted-task" },
    { item_key: "twitter:2", sync_status: "pending", sync_task_id: "persisted-task" },
  ];
  await Promise.all([coordinator.recover(rows), coordinator.recover(rows)]);
  assert.equal(fetches, 1);
  assert.equal(tracked.size, 1);
  assert.equal(coordinator.owns("youtube:1"), true);
  assert.equal(coordinator.owns("twitter:2"), true);
  tracked.get("persisted-task").onTerminal({
    task_id: "persisted-task",
    items: rows.map((row) => ({ item_key: row.item_key, status: "synced" })),
  });
  assert.equal(coordinator.owns("youtube:1"), false);
  assert.equal(terminals, 1);

  coordinator.track({
    task_id: "new-task",
    items: [{ item_key: "reddit:1", status: "syncing" }],
  }, ["reddit:1"]);
  coordinator.track({
    task_id: "new-task",
    items: [{ item_key: "zhihu:2", status: "syncing" }],
  }, ["zhihu:2"]);
  assert.equal(coordinator.owns("reddit:1"), true);
  assert.equal(coordinator.owns("zhihu:2"), true);
  tracked.get("new-task").onTerminal({
    task_id: "new-task",
    items: [{ item_key: "reddit:1", status: "synced" }, { item_key: "zhihu:2", status: "synced" }],
  });
  assert.equal(coordinator.owns("reddit:1"), false);
  assert.equal(coordinator.owns("zhihu:2"), false);
}

test("all three saved runtimes recover and deduplicate persisted nonterminal tasks", async () => {
  const popup = await import("../popup/popup-saved-sync.js");
  const mobile = await import("../../src/openbiliclaw/web/js/saved-sync-runtime.js");
  await import("../../src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js");
  const desktop = (globalThis as any).OpenBiliClawSavedSync;
  for (const createCoordinator of [
    popup.createSavedTaskCoordinator,
    mobile.createSavedTaskCoordinator,
    desktop.createSavedTaskCoordinator,
  ]) {
    assert.equal(typeof createCoordinator, "function");
    await exerciseRecoveredTaskCoordinator(createCoordinator);
  }
});

test("saved sync eligibility distinguishes content limits from rolling upgrades across all surfaces", async () => {
  const oldLocation = (globalThis as any).location;
  (globalThis as any).location = { protocol: "http:", host: "127.0.0.1:8420" };
  const popup = await import("../popup/popup-saved-sync.js");
  const mobile = await import("../../src/openbiliclaw/web/js/views/saved.js");
  (globalThis as any).location = oldLocation;
  await import("../../src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js");
  const desktop = (globalThis as any).OpenBiliClawSavedSync;

  for (const runtime of [popup, mobile, desktop]) {
    assert.equal(typeof runtime.isSavedSyncEligibleStatus, "function");
    assert.equal(runtime.isSavedSyncEligibleStatus("unsupported", "unsupported_content_type"), false);
    assert.equal(runtime.isSavedSyncEligibleStatus("unsupported", "local_only_source"), false);
    assert.equal(runtime.isSavedSyncEligibleStatus("unsupported", "unsupported_adapter_missing"), true);
    assert.equal(runtime.isSavedSyncEligibleStatus("pending", "", "task-1"), false);
    assert.equal(runtime.isSavedSyncEligibleStatus("pending", "", ""), true);
    assert.equal(runtime.isSavedSyncEligibleStatus("syncing"), false);
    assert.equal(runtime.isSavedSyncEligibleStatus(undefined), true);
    for (const retryable of ["login_required", "failed", "rate_limited"]) {
      assert.equal(runtime.isSavedSyncEligibleStatus(retryable), true);
    }
  }

  const { readFile } = await import("node:fs/promises");
  const [popupRuntime, popupView, mobileView, desktopRuntime, desktopView] = await Promise.all([
    readFile(new URL("../popup/popup-saved-sync.js", import.meta.url), "utf8"),
    readFile(new URL("../popup/popup.js", import.meta.url), "utf8"),
    readFile(new URL("../../src/openbiliclaw/web/js/views/saved.js", import.meta.url), "utf8"),
    readFile(new URL("../../src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js", import.meta.url), "utf8"),
    readFile(new URL("../../src/openbiliclaw/web/desktop/assets/js/app.js", import.meta.url), "utf8"),
  ]);
  for (const source of [popupRuntime, mobileView, desktopRuntime]) {
    assert.match(source, /仅本地保存/);
    assert.match(source, /unsupported_content_type/);
    assert.match(source, /local_only_source/);
    assert.match(source, /unsupported_adapter_missing/);
    assert.match(source, /滚动升级/);
  }
  for (const source of [popupView, mobileView, desktopView]) assert.match(source, /aria-disabled/);
});

test("popup desktop and mobile expose the same truthful saved-state matrix", async () => {
  const oldLocation = (globalThis as any).location;
  (globalThis as any).location = { protocol: "http:", host: "127.0.0.1:8420" };
  const popup = await import("../popup/popup-saved-sync.js");
  const mobile = await import("../../src/openbiliclaw/web/js/views/saved.js");
  (globalThis as any).location = oldLocation;
  await import("../../src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js");
  const desktop = (globalThis as any).OpenBiliClawSavedSync;
  const viewModels = [
    (item: any) => popup.getSavedSyncPresentation(
      item.sync_status,
      item.error_code,
      item.resolved_target,
      item.error_message,
      item.sync_task_id,
    ),
    (item: any) => mobile.getSavedSyncViewModel({
      item_key: "youtube:1", source_platform: "youtube", content_id: "1", ...item,
    }),
    (item: any) => desktop.getSavedSyncPresentation(item),
  ];

  for (const model of viewModels) {
    for (const sync_status of ["pending", "syncing"]) {
      const busy = model({
        sync_status,
        sync_task_id: sync_status === "pending" ? "task-1" : "",
      });
      assert.equal(busy.busy, true);
      assert.equal(busy.actionable, false);
      assert.match(busy.detail, /请稍候/);
    }

    const localPending = model({ sync_status: "pending", sync_task_id: "" });
    assert.equal(localPending.busy, false);
    assert.equal(localPending.actionable, true);
    assert.match(localPending.detail, /手动同步/);

    const extension = model({ sync_status: "extension_required" });
    assert.equal(extension.actionable, true);
    assert.equal(extension.actionLabel, "重试同步");
    assert.match(extension.detail, /连接已安装 OpenBiliClaw 插件/);

    const contentLimit = model({
      sync_status: "unsupported", error_code: "unsupported_content_type",
    });
    assert.equal(contentLimit.localOnly, true);
    assert.equal(contentLimit.actionable, false);

    const sourceLimit = model({
      sync_status: "unsupported", error_code: "local_only_source",
    });
    assert.equal(sourceLimit.localOnly, true);
    assert.equal(sourceLimit.actionable, false);
    assert.match(sourceLimit.detail, /仅支持本地收藏|不会向平台创建同步任务/);

    const rollingUpgrade = model({
      sync_status: "unsupported", error_code: "unsupported_adapter_missing",
    });
    assert.equal(rollingUpgrade.localOnly, false);
    assert.equal(rollingUpgrade.actionable, true);
    assert.match(rollingUpgrade.detail, /更新|升级/);

    for (const sync_status of ["login_required", "rate_limited", "failed"]) {
      const retry = model({ sync_status });
      assert.equal(retry.actionable, true);
      assert.equal(retry.actionLabel, "重试同步");
      assert.match(retry.detail, /重试|稍后/);
    }

    const success = model({ sync_status: "synced", resolved_target: "YouTube Watch Later" });
    assert.equal(success.actionable, false);
    assert.equal(success.detail, "YouTube Watch Later");
  }

  for (const platform of ["youtube", "twitter", "xiaohongshu", "douyin", "zhihu", "reddit"]) {
    const ready = mobile.getSavedSyncViewModel({
      item_key: `${platform}:1`, source_platform: platform, content_id: "1",
    });
    assert.equal(ready.label, "待同步", platform);
    assert.equal(ready.actionable, true, platform);
  }
});

function actionCard(itemKey: string, action: string, sink: string[]) {
  const button = {
    dataset: { savedAction: action },
    focus() { sink.push(itemKey); },
  };
  return {
    dataset: { itemKey },
    querySelectorAll(selector: string) { return selector === "[data-saved-action]" ? [button] : []; },
  };
}

function fallbackRoot(cards: any[], listAction: any = null, heading: any = null) {
  return {
    querySelectorAll(selector: string) { return selector === "[data-item-key]" ? cards : []; },
    querySelector(selector: string) {
      if (selector.includes("data-saved-list-action")) return listAction;
      if (selector === "[data-saved-heading]") return heading;
      return null;
    },
  };
}

test("focus restoration follows adjacent card, batch action, then heading across all surfaces", async () => {
  const popup = await import("../popup/popup-saved-sync.js");
  const mobile = await import("../../src/openbiliclaw/web/js/saved-sync-runtime.js");
  const desktop = (globalThis as any).OpenBiliClawSavedSync;
  const flows = [
    [popup.restoreSavedFocus, "popup-remove"],
    [mobile.restoreSavedFocus, "mobile-sync"],
    [desktop.restoreSavedFocus, "desktop-batch"],
  ];
  for (const [restore, label] of flows) {
    const focused: string[] = [];
    const cards = [actionCard("previous", "remove", focused), actionCard("next", "sync", focused)];
    assert.equal(restore(fallbackRoot(cards), { itemKey: "removed", action: "remove", index: 1 }), true, label);
    assert.deepEqual(focused, ["next"], label);

    focused.length = 0;
    const syncedCard = actionCard("synced", "remove", focused);
    const afterSynced = actionCard("after-synced", "sync", focused);
    assert.equal(restore(
      fallbackRoot([syncedCard, afterSynced]),
      { itemKey: "synced", action: "sync", index: 0 },
    ), true, label);
    assert.deepEqual(focused, ["after-synced"], label);

    const batch = { focus() { focused.push("batch"); } };
    assert.equal(restore(fallbackRoot([], batch), { itemKey: "removed", action: "remove", index: 0 }), true, label);
    assert.equal(focused.at(-1), "batch", label);

    const heading = { focus() { focused.push("heading"); } };
    assert.equal(restore(fallbackRoot([], null, heading), { itemKey: "removed", action: "remove", index: 0 }), true, label);
    assert.equal(focused.at(-1), "heading", label);
  }
});

test("list-level batch and retry focus tokens round-trip before card fallback on all runtimes", async () => {
  const popup = await import("../popup/popup-saved-sync.js");
  const mobile = await import("../../src/openbiliclaw/web/js/saved-sync-runtime.js");
  const desktop = (globalThis as any).OpenBiliClawSavedSync;
  for (const [capture, restore] of [
    [popup.captureSavedFocus, popup.restoreSavedFocus],
    [mobile.captureSavedFocus, mobile.restoreSavedFocus],
    [desktop.captureSavedFocus, desktop.restoreSavedFocus],
  ]) {
    for (const actionName of ["sync-all", "retry"]) {
      const focused: string[] = [];
      const listAction = {
        dataset: { savedListAction: actionName },
        closest() { return null; },
        focus() { focused.push(`list:${actionName}`); },
      };
      const card = actionCard("first-card", "remove", focused);
      const root = fallbackRoot([card], listAction);
      const token = capture(root, listAction);
      assert.deepEqual(token, { kind: "list", action: actionName });
      assert.equal(restore(root, token), true);
      assert.deepEqual(focused, [`list:${actionName}`]);
    }
  }
});

test("retry and batch handlers capture list focus before work on all three surfaces", async () => {
  const { readFile } = await import("node:fs/promises");
  const popup = await readFile(new URL("../popup/popup.js", import.meta.url), "utf8");
  const mobile = await readFile(new URL("../../src/openbiliclaw/web/js/views/saved.js", import.meta.url), "utf8");
  const desktop = await readFile(new URL("../../src/openbiliclaw/web/desktop/assets/js/app.js", import.meta.url), "utf8");
  assert.match(popup, /retry\.addEventListener\("click", \(event\) => \{[\s\S]*?captureSavedFocus[\s\S]*?loadSavedList/);
  assert.match(mobile, /saved-load-retry[\s\S]*?addEventListener\("click", \(event\) => \{[\s\S]*?captureSavedFocus[\s\S]*?load\(\)/);
  assert.match(desktop, /retry\.addEventListener\("click", \(event\) => \{[\s\S]*?captureSavedFocus[\s\S]*?reload\(\)/);
  assert.match(popup, /async function runSavedSync[\s\S]*?captureSavedFocus\(focusRoot, button\)[\s\S]*?button\.disabled = true/);
  assert.match(mobile, /saved-sync-all[\s\S]*?addEventListener\("click", \(event\) => \{[\s\S]*?captureSavedFocus\(\$root, event\.currentTarget\)[\s\S]*?runSync/);
  assert.match(desktop, /async function runDesktopSavedSync[\s\S]*?captureSavedFocus\(focusRoot, activeButton\)[\s\S]*?activeButton\.disabled = true/);
});

test("static batch buttons clear stale busy ARIA after success failure or abort reload", async () => {
  const { readFile } = await import("node:fs/promises");
  const popupRuntime = await import("../popup/popup-saved-sync.js");
  const desktopRuntime = (globalThis as any).OpenBiliClawSavedSync;
  const popup = await readFile(new URL("../popup/popup.js", import.meta.url), "utf8");
  const desktop = await readFile(
    new URL("../../src/openbiliclaw/web/desktop/assets/js/app.js", import.meta.url),
    "utf8",
  );
  assert.match(popup, /updateSavedBatchButtonState\(syncAll, pendingCount\)/);
  assert.match(desktop, /updateSavedBatchButtonState\(button, count\)/);

  for (const update of [
    popupRuntime.updateSavedBatchButtonState,
    desktopRuntime.updateSavedBatchButtonState,
  ]) {
    for (const outcome of ["success", "failure", "abort"]) {
      const attrs = new Map([
        ["aria-busy", "true"],
        ["aria-disabled", "true"],
      ]);
      const button = {
        hidden: true,
        disabled: true,
        setAttribute(name: string, value: string) { attrs.set(name, value); },
        removeAttribute(name: string) { attrs.delete(name); },
      };
      update(button, 2);
      assert.equal(button.hidden, false, outcome);
      assert.equal(button.disabled, false, outcome);
      assert.equal(attrs.get("aria-disabled"), "false", outcome);
      assert.equal(attrs.has("aria-busy"), false, outcome);
    }

    const attrs = new Map([["aria-busy", "true"]]);
    const button = {
      hidden: false,
      disabled: false,
      setAttribute(name: string, value: string) { attrs.set(name, value); },
      removeAttribute(name: string) { attrs.delete(name); },
    };
    update(button, 0);
    assert.equal(button.hidden, true);
    assert.equal(button.disabled, true);
    assert.equal(attrs.get("aria-disabled"), "true");
    assert.equal(attrs.has("aria-busy"), false);
  }

  assert.equal(popupRuntime.getSavedSyncPresentation("pending", "", "", "", "task-1").busy, true);
  assert.equal(desktopRuntime.getSavedSyncPresentation({
    sync_status: "pending",
    sync_task_id: "task-1",
  }).busy, true);
});

test("single-item submission fences exclude batch and refresh overlap on all surfaces", async () => {
  const { readFile } = await import("node:fs/promises");
  const popupRuntime = await import("../popup/popup-saved-sync.js");
  const mobileRuntime = await import("../../src/openbiliclaw/web/js/saved-sync-runtime.js");
  const desktopRuntime = (globalThis as any).OpenBiliClawSavedSync;

  for (const createFence of [
    popupRuntime.createSavedSubmissionFence,
    mobileRuntime.createSavedSubmissionFence,
    desktopRuntime.createSavedSubmissionFence,
  ]) {
    assert.equal(typeof createFence, "function");
    const fence = createFence();
    assert.equal(fence.claim(["youtube:one"]), true);
    const rows = ["youtube:one", "reddit:two"];
    assert.deepEqual(rows.filter((key) => !fence.has(key)), ["reddit:two"]);
    assert.deepEqual(rows.filter((key) => !fence.has(key)), ["reddit:two"]);
    assert.equal(fence.claim(["youtube:one"]), false);
    fence.release(["youtube:one"]);
    assert.equal(fence.has("youtube:one"), false);
  }

  const popup = await readFile(new URL("../popup/popup.js", import.meta.url), "utf8");
  const mobile = await readFile(
    new URL("../../src/openbiliclaw/web/js/views/saved.js", import.meta.url),
    "utf8",
  );
  const desktop = await readFile(
    new URL("../../src/openbiliclaw/web/desktop/assets/js/app.js", import.meta.url),
    "utf8",
  );
  assert.match(popup, /syncEligible[\s\S]*?submissions\.has[\s\S]*?submissions\.claim/);
  assert.match(mobile, /selected = selected\.filter[\s\S]*?!syncingKeys\.has[\s\S]*?syncingKeys\.claim/);
  assert.match(desktop, /const eligible = selected\.filter[\s\S]*?!desktopSyncingKeys\[listKind\]\.has[\s\S]*?desktopSyncingKeys\[listKind\]\.claim/);
});

test("pre-task reload stays busy and mobile failure rerenders retryable live status", async () => {
  const { readFile } = await import("node:fs/promises");
  const popup = await readFile(new URL("../popup/popup.js", import.meta.url), "utf8");
  const mobile = await readFile(
    new URL("../../src/openbiliclaw/web/js/views/saved.js", import.meta.url),
    "utf8",
  );
  assert.match(
    popup,
    /function buildSavedCard[\s\S]*?submissions\.has\(item\.item_key\)[\s\S]*?sync_status: "syncing"/,
  );
  const runSyncStart = mobile.indexOf("  async function runSync(");
  const runSyncEnd = mobile.indexOf("\n  function renderList()", runSyncStart);
  assert.notEqual(runSyncStart, -1);
  assert.notEqual(runSyncEnd, -1);
  const runSync = mobile.slice(runSyncStart, runSyncEnd);
  assert.match(
    runSync,
    /catch \(error\) \{[\s\S]*?messageIsError = true;[\s\S]*?finally \{[\s\S]*?syncingKeys\.release\(selectedKeys\);[\s\S]*?if \(!submitted\) \{[\s\S]*?renderList\(\)/,
  );
  assert.match(
    mobile,
    /saved-sync-message" aria-live="polite" \$\{messageIsError \? 'role="alert"'/,
  );
});

test("Task 8 save and sync controls reserve coarse-pointer size without label shift", async () => {
  const { readFile } = await import("node:fs/promises");
  const popupCss = await readFile(new URL("../popup/popup.html", import.meta.url), "utf8");
  const desktopCss = await readFile(new URL("../../src/openbiliclaw/web/desktop/assets/css/app.css", import.meta.url), "utf8");
  for (const css of [popupCss, desktopCss]) {
    assert.match(css, /\(pointer:\s*coarse\)[\s\S]*?(saved-toggle|watch-later-btn)[\s\S]*?44px/);
    assert.match(css, /(saved-sync-all|watchLaterSyncAll)[\s\S]*?min-inline-size/);
  }
  const mobileCss = await readFile(new URL("../../src/openbiliclaw/web/css/app.css", import.meta.url), "utf8");
  assert.match(popupCss, /saved-card-sync[\s\S]*?min-inline-size/);
  assert.match(mobileCss, /saved-card-sync[\s\S]*?min-inline-size/);
  assert.match(desktopCss, /saved-sync-one[\s\S]*?min-inline-size/);
});

test("saved task lifecycles dispose on page teardown and mobile binds visibility once", async () => {
  const { readFile } = await import("node:fs/promises");
  const popup = await readFile(new URL("../popup/popup.js", import.meta.url), "utf8");
  const mobile = await readFile(new URL("../../src/openbiliclaw/web/js/views/saved.js", import.meta.url), "utf8");
  const desktop = await readFile(new URL("../../src/openbiliclaw/web/desktop/assets/js/app.js", import.meta.url), "utf8");
  for (const source of [popup, mobile, desktop]) {
    assert.match(source, /addEventListener\("pagehide"[\s\S]*?\.dispose\(\)/);
  }
  assert.match(mobile, /let visibilityBound = false[\s\S]*?if \(!visibilityBound\)/);
  assert.match(desktop, /wlBtn\.title = cached\.watchLater \? "取消稍后再看" : "稍后再看"/);
});

test("disposing a task tracker suppresses callbacks from an in-flight poll", async () => {
  const popup = await import("../popup/popup-saved-sync.js");
  const mobile = await import("../../src/openbiliclaw/web/js/saved-sync-runtime.js");
  const desktop = (globalThis as any).OpenBiliClawSavedSync;
  for (const createTracker of [
    popup.createSavedSyncTaskTracker,
    mobile.createDurableTaskTracker,
    desktop.createDurableTaskTracker,
  ]) {
    let scheduled: (() => Promise<void>) | null = null;
    let finishPoll: (task: any) => void = () => {};
    let callbacks = 0;
    const tracker = createTracker({
      poll: () => new Promise((resolve) => { finishPoll = resolve; }),
      schedule: (run: () => Promise<void>) => { scheduled = run; return 1; },
      cancel: () => {},
    });
    tracker.track({
      task_id: "dispose-task",
      items: [{ item_key: "youtube:1", status: "syncing" }],
    }, {
      onProgress: () => { callbacks += 1; },
      onTerminal: () => { callbacks += 1; },
      onPollError: () => { callbacks += 1; },
    });
    callbacks = 0;
    const inFlight = scheduled?.();
    tracker.dispose();
    finishPoll({
      task_id: "dispose-task",
      items: [{ item_key: "youtube:1", status: "synced" }],
    });
    await inFlight;
    assert.equal(callbacks, 0);
  }
});
