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
