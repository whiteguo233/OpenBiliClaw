/**
 * Tests for the Bilibili extension-search task dispatcher.
 *
 * Pure helpers are tested directly. The chrome.tabs lifecycle mirrors the
 * XHS/YT dispatchers and is covered through the same helper shape here.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildBiliExecuteMessageData,
  buildBiliTaskUrl,
  computeBiliTaskTimeoutMs,
  executeTask,
  handleBiliTaskResult,
  isValidBiliTask,
  pollBiliTaskNow,
  type BiliTask,
} from "../src/background/bili-task-dispatcher.ts";

test("buildBiliTaskUrl encodes keyword search URL with page metadata", () => {
  const task: BiliTask = {
    id: "bili-1",
    type: "search",
    query: "机械键盘 声音",
    page: 2,
  };

  assert.equal(
    buildBiliTaskUrl(task),
    "https://search.bilibili.com/all?keyword=%E6%9C%BA%E6%A2%B0%E9%94%AE%E7%9B%98%20%E5%A3%B0%E9%9F%B3&page=2",
  );
});

test("buildBiliTaskUrl returns null for invalid or unsupported tasks", () => {
  assert.equal(buildBiliTaskUrl({ id: "x", type: "search" }), null);
  assert.equal(buildBiliTaskUrl({ id: "x", type: "unknown" as never, query: "猫" }), null);
});

test("isValidBiliTask accepts search tasks with a non-empty query", () => {
  assert.equal(
    isValidBiliTask({
      id: "bili-2",
      type: "search",
      query: "咖啡 手冲",
      limit: 20,
      page_size: 30,
    }),
    true,
  );
});

test("isValidBiliTask rejects malformed payloads", () => {
  assert.equal(isValidBiliTask(null), false);
  assert.equal(isValidBiliTask({}), false);
  assert.equal(isValidBiliTask({ id: "", type: "search", query: "猫" }), false);
  assert.equal(isValidBiliTask({ id: "x", type: "search", query: "" }), false);
  assert.equal(isValidBiliTask({ id: "x", type: "search", query: "猫", limit: 0 }), false);
  assert.equal(isValidBiliTask({ id: "x", type: "creator", query: "猫" }), false);
});

test("computeBiliTaskTimeoutMs gives rendered search pages enough time", () => {
  assert.equal(
    computeBiliTaskTimeoutMs({ id: "bili-3", type: "search", query: "猫" }),
    90_000,
  );
});

test("buildBiliExecuteMessageData includes only executor fields", () => {
  const data = buildBiliExecuteMessageData({
    id: "bili-4",
    type: "search",
    query: "机械键盘",
    limit: 12,
    page_size: 30,
    source_keyword_id: 99,
  });

  assert.deepEqual(data, {
    task_id: "bili-4",
    type: "search",
    query: "机械键盘",
    limit: 12,
    page_size: 30,
  });
});

test("pollBiliTaskNow exists as the WS-driven immediate-poll entry point", () => {
  assert.equal(typeof pollBiliTaskNow, "function");
  assert.doesNotThrow(() => pollBiliTaskNow());
});

interface TabUpdatedListener {
  (tabId: number, changeInfo: { status?: string }): void;
}

interface ChromeMock {
  tabs: {
    create: (opts: { url: string; active?: boolean }) => Promise<{ id: number }>;
    get: (tabId: number) => Promise<{ id: number; status?: string }>;
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
  removedTabs: number[];
  tabStatus: string;
}

function installChromeMock(): MockState {
  const state: MockState = {
    createdTabs: [],
    sentMessages: [],
    sendMessageImpl: async () => {},
    fetchCalls: [],
    removedTabs: [],
    tabStatus: "loading",
  };

  const listeners: TabUpdatedListener[] = [];
  const chromeMock: ChromeMock = {
    tabs: {
      create: async ({ url, active }) => {
        state.createdTabs.push({ url, active });
        return { id: 42 };
      },
      get: async (tabId) => ({ id: tabId, status: state.tabStatus }),
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

async function flush(): Promise<void> {
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
}

test("executeTask retries Bili sendMessage until the content script listener is ready", async () => {
  const state = installChromeMock();
  const chrome = (globalThis as unknown as { chrome: ChromeMock }).chrome;
  let attempts = 0;
  state.sendMessageImpl = async () => {
    attempts += 1;
    if (attempts === 1) {
      throw new Error("Could not establish connection. Receiving end does not exist.");
    }
  };

  const task: BiliTask = { id: "bili-retry", type: "search", query: "机械键盘 声音" };
  await executeTask(task);

  state.tabStatus = "complete";
  chrome.tabs.onUpdated._emit(42, { status: "complete" });
  await flush();

  assert.equal(state.sentMessages.length, 1);
  assert.equal(state.fetchCalls.length, 0, "first missing receiver should not fail the task");

  await new Promise((r) => setTimeout(r, 300));
  await flush();

  assert.equal(state.sentMessages.length, 2);
  assert.equal(state.fetchCalls.length, 0, "successful retry should not post a failure");

  await handleBiliTaskResult({ task_id: "bili-retry", status: "ok", videos: [] });
  await flush();
  assert.equal(state.removedTabs.length, 1);
});
