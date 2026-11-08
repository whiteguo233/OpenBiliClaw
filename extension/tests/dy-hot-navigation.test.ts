import test from "node:test";
import assert from "node:assert/strict";

import {
  executeTask,
  handleDyHotResult,
  resetDyTaskStateForTest,
} from "../src/background/dy-task-dispatcher.ts";
import { installChromeMock } from "./helpers/chrome-mock.ts";

const task = {
  id: "two-hot-words",
  type: "hot" as const,
  max_items: 12,
  hot_items: [
    { sentence_id: "first", word: "第一个热点" },
    { sentence_id: "second", word: "第二个热点" },
  ],
};

function installBrowser() {
  const state = installChromeMock();
  // Model Chrome's callback overload as well as its Promise overload. The
  // production hot dispatcher used the callback overload before this repair.
  const update = globalThis.chrome.tabs.update;
  globalThis.chrome.tabs.update = (async (
    id: number, properties: chrome.tabs.UpdateProperties,
    callback?: (tab: chrome.tabs.Tab | undefined) => void,
  ) => {
    const tab = await update(id, properties);
    callback?.(tab);
    return tab;
  }) as typeof update;
  return state;
}

async function settle() {
  for (let i = 0; i < 10; i += 1) await new Promise(resolve => setTimeout(resolve, 0));
}

test("hot discovery submits from the ready page without reloading its homepage", async () => {
  const state = installBrowser();
  try {
    await executeTask(task);
    await settle();
    assert.equal(state.sentMessages.length, 1);
    assert.equal(state.updatedTabs.filter(tab => tab.url).length, 1,
      "only the initial muted blank-to-home navigation is necessary");
  } finally {
    resetDyTaskStateForTest();
    state.restore();
  }
});

test("hot search resumes its collector after full navigation instead of timing out", async () => {
  const state = installBrowser();
  try {
    await executeTask(task);
    await settle();
    state.emitTabUpdated(42, {
      url: `https://www.douyin.com/root/search/${encodeURIComponent(task.hot_items[0].word)}`,
      status: "loading",
    });
    await settle();
    state.emitTabUpdated(42, { status: "complete" });
    await settle();
    const messages = state.sentMessages.map(call => call.message as {
      action: string; data: { sentence_id: string; resume_after_navigation?: boolean };
    });
    assert.equal(messages.length, 2, "the new document needs a collector execution");
    assert.equal(messages[1].action, "DY_HOT_EXECUTE");
    assert.equal(messages[1].data.sentence_id, "first");
    assert.equal(messages[1].data.resume_after_navigation, true,
      "resume collection without submitting another upstream search");
  } finally {
    resetDyTaskStateForTest();
    state.restore();
  }
});

test("the second hot word submits without an intervening homepage reload", async () => {
  const state = installBrowser();
  try {
    await executeTask(task);
    await settle();
    const navigations = state.updatedTabs.filter(tab => tab.url).length;
    await handleDyHotResult({
      task_id: task.id, sentence_id: "first", word: task.hot_items[0].word,
      status: "ok", scope_count: 1,
      items: [{ scope: "dy_hot", aweme_id: "video-first", title: "第一词视频" }],
    });
    await settle();
    assert.equal(state.updatedTabs.filter(tab => tab.url).length, navigations);
    const next = state.sentMessages.at(-1)?.message as { data: { sentence_id: string; max_items: number } };
    assert.equal(next.data.sentence_id, "second");
    assert.equal(next.data.max_items, 11, "request only the remaining total budget");
  } finally {
    resetDyTaskStateForTest();
    state.restore();
  }
});

test("a blocked hot word preserves its newly collected partial before terminating", async () => {
  const state = installBrowser();
  try {
    await executeTask(task);
    await settle();
    await handleDyHotResult({
      task_id: task.id, sentence_id: "first", word: task.hot_items[0].word,
      status: "failed", error: "api_rate_limited", scope_count: 1,
      items: [{ scope: "dy_hot", aweme_id: "valid-before-block", title: "已取得的视频" }],
    });
    const posts = state.fetchCalls.map(call => call.body as { status: string; videos?: { aweme_id: string }[] });
    assert.deepEqual(posts.map(body => body.status), ["partial", "failed"]);
    assert.equal(posts[0].videos?.[0].aweme_id, "valid-before-block");
    assert.equal(state.sentMessages.length, 1, "never start the next word after a block");
  } finally {
    resetDyTaskStateForTest();
    state.restore();
  }
});

test("a late hot navigation callback cannot resubmit while a failed partial awaits ACK", async () => {
  const state = installBrowser();
  let releaseInjection = () => {};
  let releaseAck = () => {};
  let completion: Promise<void> | undefined;
  try {
    await executeTask(task);
    await settle();
    const executeScript = globalThis.chrome.scripting.executeScript;
    const injectionGate = new Promise<void>(resolve => { releaseInjection = resolve; });
    globalThis.chrome.scripting.executeScript = (async (...args: Parameters<typeof executeScript>) => {
      await injectionGate;
      return executeScript(...args);
    }) as typeof executeScript;
    state.emitTabUpdated(42, {
      url: `https://www.douyin.com/root/search/${encodeURIComponent(task.hot_items[0].word)}`,
      status: "complete",
    });
    await settle();
    const fetchImpl = state.fetchImpl;
    const ackGate = new Promise<void>(resolve => { releaseAck = resolve; });
    let first = true;
    state.fetchImpl = async (...args) => {
      if (first) { first = false; await ackGate; }
      return fetchImpl(...args);
    };
    completion = handleDyHotResult({
      task_id: task.id, sentence_id: "first", word: task.hot_items[0].word,
      status: "failed", error: "api_rate_limited", scope_count: 1,
      items: [{ scope: "dy_hot", aweme_id: "valid-before-block", title: "已取得的视频" }],
    });
    await settle();
    releaseInjection();
    await settle();
    assert.equal(state.sentMessages.length, 1,
      "a result invalidates any navigation callback before waiting for its ACK");
  } finally {
    releaseInjection();
    releaseAck();
    await completion;
    resetDyTaskStateForTest();
    state.restore();
  }
});
