import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  computeZhihuTaskTimeoutMs,
  dispatchZhihuNativeSaveTask,
  executeTask,
  handleZhihuTaskResult,
  isValidZhihuTask,
  postZhihuNativeSaveResult,
  type ZhihuTask,
} from "../src/background/zhihu-task-dispatcher.ts";
import type { NativeSaveResult, NativeSaveTask } from "../src/shared/native-save.ts";
import { installChromeMock } from "./helpers/chrome-mock.ts";

test("Zhihu content entry installs the read-only native-save verifier", () => {
  const source = readFileSync(resolve("src/content/zhihu.ts"), "utf8");
  assert.match(source, /installNativeSaveExecutor\(["']zhihu["'], saveZhihu, verifyZhihu\)/);
  assert.ok(source.indexOf("installNativeSaveExecutor(") < source.indexOf("startCollector("));
  assert.match(source, /shouldStartPassiveCollector\(\)[\s\S]*if \(shouldStart\) startCollector/);
});

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

test("isValidZhihuTask accepts only the Zhihu native-save union branch", () => {
  const task: NativeSaveTask = {
    id: "123e4567-e89b-42d3-a456-426614174008",
    type: "native_save",
    platform: "zhihu",
    platform_slug: "zhihu",
    item_key: "zhihu:answer:2002",
    content_id: "answer:2002",
    content_url: "https://www.zhihu.com/question/101/answer/2002",
    content_type: "answer",
    requested_action: "favorite",
    resolved_action: "favorite",
    target_label: "OpenBiliClaw",
  };
  assert.equal(isValidZhihuTask(task), true);
  assert.equal(isValidZhihuTask({ ...task, platform_slug: "x" }), false);
});

test("Zhihu native dispatcher runs shared runner with exact slug and authenticated result closure", async () => {
  const task: NativeSaveTask = {
    id: "123e4567-e89b-42d3-a456-426614174008",
    type: "native_save",
    platform: "zhihu",
    platform_slug: "zhihu",
    item_key: "zhihu:article:3003",
    content_id: "article:3003",
    content_url: "https://zhuanlan.zhihu.com/p/3003",
    content_type: "article",
    requested_action: "watch_later",
    resolved_action: "favorite",
    target_label: "OpenBiliClaw",
  };
  const posted: NativeSaveResult[] = [];
  let slug = "";
  await dispatchZhihuNativeSaveTask(task, {
    async run(received, platformSlug, postResult) {
      assert.equal(received, task);
      slug = platformSlug;
      await postResult({
        task_id: task.id,
        item_key: task.item_key,
        status: "synced",
        error_code: "",
        error_message: "",
      });
    },
    async postResult(result) { posted.push(result); },
  });
  assert.equal(slug, "zhihu");
  assert.equal(posted.length, 1);

  const calls: Array<{ url: string; init: RequestInit }> = [];
  await postZhihuNativeSaveResult(posted[0], undefined, {
    resolveUrl: async (path) => `http://127.0.0.1:8420/api${path}`,
    async fetch(url, init) { calls.push({ url, init }); return { ok: true }; },
  });
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/sources/zhihu/task-result");
  assert.equal(calls[0].init.method, "POST");
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), posted[0]);
  await assert.rejects(postZhihuNativeSaveResult(posted[0], undefined, {
    resolveUrl: async (path) => `http://127.0.0.1:8420/api${path}`,
    async fetch() { return { ok: false }; },
  }), /not acknowledged/);
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
