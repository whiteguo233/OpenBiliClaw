import test from "node:test";
import assert from "node:assert/strict";

import {
  computeZhihuTaskTimeoutMs,
  executeTask,
  handleZhihuTaskResult,
  isValidZhihuTask,
  type ZhihuTask,
} from "../src/background/zhihu-task-dispatcher.ts";
import { installChromeMock } from "./helpers/chrome-mock.ts";

test("isValidZhihuTask accepts discovery task types", () => {
  assert.equal(isValidZhihuTask({ id: "hot", type: "hot", max_items: 10 }), true);
  assert.equal(isValidZhihuTask({ id: "feed", type: "feed", max_items: 10 }), true);
  assert.equal(
    isValidZhihuTask({
      id: "creator",
      type: "creator",
      creator_urls: ["https://www.zhihu.com/people/demo"],
      max_items_per_creator: 5,
    }),
    true,
  );
  assert.equal(
    isValidZhihuTask({
      id: "related",
      type: "related",
      related_urls: ["https://www.zhihu.com/question/1"],
      max_items_per_seed: 5,
    }),
    true,
  );
});

test("isValidZhihuTask rejects malformed discovery tasks", () => {
  assert.equal(isValidZhihuTask({ id: "hot", type: "hot", max_items: 0 }), false);
  assert.equal(isValidZhihuTask({ id: "creator", type: "creator", creator_urls: [] }), false);
  assert.equal(isValidZhihuTask({ id: "related", type: "related", related_urls: [] }), false);
});

test("computeZhihuTaskTimeoutMs scales discovery task breadth", () => {
  assert.ok(
    computeZhihuTaskTimeoutMs({
      id: "creator",
      type: "creator",
      creator_urls: ["a", "b", "c"],
    }) > computeZhihuTaskTimeoutMs({ id: "feed", type: "feed" }),
  );
});

async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

test("executeTask opens init bootstrap in foreground and discovery tasks in background", async () => {
  const chromeMock = installChromeMock();
  try {
    const initTask: ZhihuTask = { id: "zhihu-init", type: "bootstrap_events" };
    await executeTask(initTask);
    await flush();

    assert.equal(chromeMock.createdTabs.at(-1)?.active, true);

    await handleZhihuTaskResult({
      task_id: "zhihu-init",
      status: "ok",
      items: [],
      scope_counts: {},
    });
    await flush();

    const discoveryTask: ZhihuTask = { id: "zhihu-search", type: "search", keywords: ["AI"] };
    await executeTask(discoveryTask);
    await flush();

    const discoveryTabActive = chromeMock.createdTabs.at(-1)?.active;

    await handleZhihuTaskResult({
      task_id: "zhihu-search",
      status: "ok",
      items: [],
      scope_counts: {},
    });
    await flush();

    assert.equal(discoveryTabActive, false);
  } finally {
    chromeMock.restore();
  }
});
