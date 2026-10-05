/**
 * Tests for the shared auto-opened task-tab mute helper (issue #163).
 */

import test from "node:test";
import assert from "node:assert/strict";

import { createTaskTab } from "../src/background/task-tab.ts";
import { installChromeMock } from "./helpers/chrome-mock.ts";

test("createTaskTab creates the tab and immediately mutes it through tabs.update", async () => {
  const state = installChromeMock();
  try {
    const tab = await createTaskTab({ url: "https://www.douyin.com/", active: false });

    assert.equal(tab.id, 42);
    assert.deepEqual(state.createdTabs, [
      { url: "https://www.douyin.com/", active: false },
    ]);
    assert.deepEqual(state.updatedTabs, [{ tabId: 42, muted: true }]);
  } finally {
    state.restore();
  }
});

test("createTaskTab preserves active foreground task tabs while muting them", async () => {
  const state = installChromeMock();
  try {
    await createTaskTab({ url: "https://www.xiaohongshu.com/explore", active: true });

    assert.deepEqual(state.createdTabs, [
      { url: "https://www.xiaohongshu.com/explore", active: true },
    ]);
    assert.deepEqual(state.updatedTabs, [{ tabId: 42, muted: true }]);
  } finally {
    state.restore();
  }
});

test("createTaskTab still returns a usable tab when the engine rejects muting", async () => {
  const state = installChromeMock();
  const chromeMock = (globalThis as unknown as { chrome: typeof chrome }).chrome;
  const originalUpdate = chromeMock.tabs.update;
  try {
    chromeMock.tabs.update = async () => {
      throw new Error("muted updates are unsupported in this engine");
    };

    const tab = await createTaskTab({ url: "https://example.com/", active: false });

    assert.equal(tab.id, 42);
    assert.equal(state.createdTabs.length, 1);
  } finally {
    chromeMock.tabs.update = originalUpdate;
    state.restore();
  }
});

test("createTaskTab leaves a tab without an id untouched", async () => {
  const state = installChromeMock();
  const chromeMock = (globalThis as unknown as { chrome: typeof chrome }).chrome;
  const originalCreate = chromeMock.tabs.create;
  try {
    chromeMock.tabs.create = async () => ({ url: "https://example.com/" });

    const tab = await createTaskTab({ url: "https://example.com/", active: false });

    assert.equal(tab.id, undefined);
    assert.deepEqual(state.updatedTabs, []);
  } finally {
    chromeMock.tabs.create = originalCreate;
    state.restore();
  }
});
