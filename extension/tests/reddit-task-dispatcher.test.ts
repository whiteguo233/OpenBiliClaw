import test from "node:test";
import assert from "node:assert/strict";

import {
  computeRedditTaskTimeoutMs,
  executeTask,
  handleRedditTaskResult,
  isValidRedditTask,
  shouldOpenRedditTaskActive,
  type RedditTask,
} from "../src/background/reddit-task-dispatcher.ts";
import { installChromeMock } from "./helpers/chrome-mock.ts";

test("isValidRedditTask accepts all discovery task types", () => {
  assert.equal(isValidRedditTask({ id: "search", type: "search", keywords: ["AI"] }), true);
  assert.equal(isValidRedditTask({ id: "hot", type: "hot", subreddit: "all" }), true);
  assert.equal(
    isValidRedditTask({
      id: "subreddit",
      type: "subreddit",
      subreddits: ["LocalLLaMA"],
    }),
    true,
  );
  assert.equal(
    isValidRedditTask({
      id: "related",
      type: "related",
      related_urls: ["https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/"],
    }),
    true,
  );
  assert.equal(
    isValidRedditTask({
      id: "bootstrap",
      type: "bootstrap_events",
      max_items_per_scope: 300,
    }),
    true,
  );
});

test("isValidRedditTask rejects malformed tasks", () => {
  assert.equal(isValidRedditTask({ id: "", type: "search", keywords: ["AI"] }), false);
  assert.equal(isValidRedditTask({ id: "search", type: "search", keywords: [] }), false);
  assert.equal(isValidRedditTask({ id: "subreddit", type: "subreddit", subreddits: [] }), false);
  assert.equal(isValidRedditTask({ id: "related", type: "related", related_urls: [] }), false);
});

test("computeRedditTaskTimeoutMs scales with breadth", () => {
  assert.ok(
    computeRedditTaskTimeoutMs({
      id: "related",
      type: "related",
      related_urls: ["a", "b", "c"],
    }) > computeRedditTaskTimeoutMs({ id: "hot", type: "hot" }),
  );
});

async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

test("executeTask opens Reddit task tabs in background", async () => {
  const chromeMock = installChromeMock();
  try {
    const task: RedditTask = { id: "reddit-search", type: "search", keywords: ["AI"] };
    await executeTask(task);
    await flush();

    assert.equal(chromeMock.createdTabs.at(-1)?.active, false);

    await handleRedditTaskResult({
      task_id: "reddit-search",
      status: "ok",
      items: [],
      scope_counts: {},
    });
    await flush();
  } finally {
    chromeMock.restore();
  }
});

test("executeTask opens Reddit bootstrap task tabs active", async () => {
  const chromeMock = installChromeMock();
  try {
    const task: RedditTask = { id: "reddit-bootstrap", type: "bootstrap_events" };
    assert.equal(shouldOpenRedditTaskActive(task), true);

    await executeTask(task);
    await flush();

    assert.equal(chromeMock.createdTabs.at(-1)?.active, true);

    await handleRedditTaskResult({
      task_id: "reddit-bootstrap",
      status: "ok",
      items: [],
      scope_counts: {},
    });
    await flush();
  } finally {
    chromeMock.restore();
  }
});

test("executeTask injects Reddit content script before execute message", async () => {
  const chromeMock = installChromeMock();
  try {
    const task: RedditTask = { id: "reddit-inject", type: "bootstrap_events" };
    await executeTask(task);
    await flush();
    const actual = chromeMock.executedScripts.at(-1);
    const tabId = chromeMock.sentMessages.at(-1)?.tabId;

    await handleRedditTaskResult({
      task_id: "reddit-inject",
      status: "ok",
      items: [],
      scope_counts: {},
    });
    await flush();

    assert.deepEqual(actual, {
      files: ["dist/content/reddit.js"],
      tabId,
      world: "ISOLATED",
    });
  } finally {
    chromeMock.restore();
  }
});

test("executeTask retries Reddit sendMessage until the content script listener is ready", async () => {
  const chromeMock = installChromeMock();
  let attempts = 0;
  chromeMock.sendMessageImpl = async () => {
    attempts += 1;
    if (attempts === 1) {
      throw new Error("listener not ready");
    }
    return {};
  };

  try {
    const task: RedditTask = { id: "reddit-retry", type: "subreddit", subreddits: ["LocalLLaMA"] };
    await executeTask(task);
    await new Promise((resolve) => setTimeout(resolve, 350));

    assert.equal(attempts, 2);

    await handleRedditTaskResult({
      task_id: "reddit-retry",
      status: "ok",
      items: [],
      scope_counts: {},
    });
    await flush();
  } finally {
    chromeMock.restore();
  }
});
