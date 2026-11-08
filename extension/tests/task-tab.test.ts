/**
 * Tests for the shared auto-opened task-tab mute helper (issue #163).
 */

import test from "node:test";
import assert from "node:assert/strict";

import { createTaskTab } from "../src/background/task-tab.ts";
import { installChromeMock } from "./helpers/chrome-mock.ts";

test("task pages cannot load upstream media before muting succeeds", async () => {
  const state = installChromeMock();
  const chromeMock = globalThis.chrome;
  const create = chromeMock.tabs.create;
  const update = chromeMock.tabs.update;
  const transitions: Array<{ url: string; muted: boolean }> = [];
  let muted = false;
  try {
    chromeMock.tabs.create = async (properties) => {
      transitions.push({ url: properties.url ?? "about:blank", muted });
      return create(properties);
    };
    chromeMock.tabs.update = (async (tabId: number, properties: chrome.tabs.UpdateProperties) => {
      if (properties.muted !== undefined) muted = properties.muted;
      if (properties.url) transitions.push({ url: properties.url, muted });
      return update(tabId, properties);
    }) as typeof update;

    await createTaskTab({ url: "https://www.douyin.com/", active: false });

    assert.deepEqual(transitions, [
      { url: "about:blank", muted: false },
      { url: "https://www.douyin.com/", muted: true },
    ]);
  } finally {
    state.restore();
  }
});

test("a mute failure closes the blank task tab without loading the website", async () => {
  const state = installChromeMock();
  const chromeMock = globalThis.chrome;
  try {
    chromeMock.tabs.update = async () => { throw new Error("mute failed"); };

    await assert.rejects(
      createTaskTab({ url: "https://www.douyin.com/", active: false }),
      /mute failed/,
    );

    assert.deepEqual(state.createdTabs, [{ url: "about:blank", active: false }]);
    assert.deepEqual(state.removedTabs, [42]);
  } finally {
    state.restore();
  }
});

test("createTaskTab returns the requested page after muting the blank tab", async () => {
  const state = installChromeMock();
  try {
    const tab = await createTaskTab({ url: "https://www.douyin.com/", active: false });

    assert.equal(tab.id, 42);
    assert.deepEqual(state.createdTabs, [
      { url: "about:blank", active: false },
    ]);
    assert.equal(tab.url, "https://www.douyin.com/");
    assert.deepEqual(state.updatedTabs, [
      { tabId: 42, muted: true },
      { tabId: 42, url: "https://www.douyin.com/" },
    ]);
  } finally {
    state.restore();
  }
});

test("createTaskTab preserves active foreground task tabs while muting them", async () => {
  const state = installChromeMock();
  try {
    await createTaskTab({ url: "https://www.xiaohongshu.com/explore", active: true });

    assert.deepEqual(state.createdTabs, [
      { url: "about:blank", active: true },
    ]);
    assert.deepEqual(state.updatedTabs, [
      { tabId: 42, muted: true },
      { tabId: 42, url: "https://www.xiaohongshu.com/explore" },
    ]);
  } finally {
    state.restore();
  }
});

test("a navigation failure cleans up the already muted tab", async () => {
  const state = installChromeMock();
  const chromeMock = globalThis.chrome;
  const update = chromeMock.tabs.update;
  try {
    chromeMock.tabs.update = (async (tabId: number, properties: chrome.tabs.UpdateProperties) => {
      if (properties.url) throw new Error("navigation failed");
      return update(tabId, properties);
    }) as typeof update;
    await assert.rejects(createTaskTab({ url: "https://example.com/", active: false }), /navigation failed/);
    assert.deepEqual(state.removedTabs, [42]);
  } finally {
    state.restore();
  }
});

test("createTaskTab rejects a blank tab without an id", async () => {
  const state = installChromeMock();
  const chromeMock = (globalThis as unknown as { chrome: typeof chrome }).chrome;
  const originalCreate = chromeMock.tabs.create;
  try {
    chromeMock.tabs.create = async () => ({ url: "about:blank" });

    await assert.rejects(
      createTaskTab({ url: "https://example.com/", active: false }), /task_tab_id_unknown/,
    );
    assert.deepEqual(state.updatedTabs, []);
  } finally {
    chromeMock.tabs.create = originalCreate;
    state.restore();
  }
});

test("blank task tabs keep their positioning properties and need no URL navigation", async () => {
  const state = installChromeMock();
  try {
    await createTaskTab({ url: "about:blank", active: false, windowId: 8, index: 2, pinned: true });
    assert.deepEqual(state.createdTabs, [{ url: "about:blank", active: false, windowId: 8, index: 2, pinned: true }]);
    assert.deepEqual(state.updatedTabs, [{ tabId: 42, muted: true }]);
  } finally {
    state.restore();
  }
});
