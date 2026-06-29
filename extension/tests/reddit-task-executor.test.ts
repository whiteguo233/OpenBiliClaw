import test from "node:test";
import assert from "node:assert/strict";

import {
  buildRedditJsonUrl,
  collectRedditListingItems,
  executeRedditTask,
  installRedditMessageListener,
  normalizeRedditListingChild,
} from "../src/content/reddit/task-executor.ts";

test("normalizeRedditListingChild maps Reddit listing posts", () => {
  const item = normalizeRedditListingChild(
    {
      kind: "t3",
      data: {
        id: "abc123",
        name: "t3_abc123",
        title: "Local-first agents",
        permalink: "/r/LocalLLaMA/comments/abc123/local_first_agents/",
        subreddit: "LocalLLaMA",
        author: "agent_builder",
        score: 42,
        num_comments: 7,
        selftext: "A practical write-up.",
      },
    },
    { scope: "reddit_search", strategy: "reddit-search", searchKeyword: "agents" },
  );

  assert.deepEqual(item, {
    scope: "reddit_search",
    content_type: "post",
    id: "abc123",
    name: "t3_abc123",
    title: "Local-first agents",
    url: "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
    permalink: "/r/LocalLLaMA/comments/abc123/local_first_agents/",
    subreddit: "LocalLLaMA",
    author: "agent_builder",
    score: 42,
    num_comments: 7,
    selftext: "A practical write-up.",
    search_keyword: "agents",
    source_strategy: "reddit-search",
  });
});

test("collectRedditListingItems extracts children from listing wrappers", () => {
  const rows = collectRedditListingItems(
    {
      kind: "Listing",
      data: {
        children: [
          { kind: "t3", data: { id: "a", title: "A", permalink: "/r/a/comments/a/a/" } },
          { kind: "t1", data: { id: "b", body: "B", permalink: "/r/a/comments/a/a/b/" } },
        ],
      },
    },
    { scope: "reddit_hot", strategy: "reddit-hot" },
  );

  assert.equal(rows.length, 2);
  assert.equal(rows[0]?.content_type, "post");
  assert.equal(rows[1]?.content_type, "comment");
});

test("buildRedditJsonUrl builds same-origin task endpoints", () => {
  assert.equal(
    buildRedditJsonUrl("search", "local agents", 5),
    "https://www.reddit.com/search.json?q=local+agents&limit=5&sort=relevance",
  );
  assert.equal(
    buildRedditJsonUrl("hot", "LocalLLaMA", 5),
    "https://www.reddit.com/r/LocalLLaMA/hot.json?limit=5",
  );
  assert.equal(
    buildRedditJsonUrl("related", "https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/", 5),
    "https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/.json?limit=5",
  );
});

test("executeRedditTask collects Reddit bootstrap saved, upvoted, and subscribed signals", async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = (async (input: string | URL | Request) => {
    const url = String(input);
    calls.push(url);
    const json = url.includes("/api/me.json")
      ? { data: { name: "agent_user" } }
      : url.includes("/saved.json")
        ? {
            data: {
              children: [
                {
                  kind: "t3",
                  data: {
                    id: "saved1",
                    title: "Saved local agent note",
                    permalink: "/r/LocalLLaMA/comments/saved1/title/",
                    subreddit: "LocalLLaMA",
                    author: "author1",
                  },
                },
              ],
            },
          }
        : url.includes("/upvoted.json")
          ? {
              data: {
                children: [
                  {
                    kind: "t1",
                    data: {
                      id: "up1",
                      body: "Useful benchmark comment",
                      permalink: "/r/LocalLLaMA/comments/post/title/up1/",
                      subreddit: "LocalLLaMA",
                      author: "author2",
                    },
                  },
                ],
              },
            }
          : {
              data: {
                children: [
                  {
                    kind: "t5",
                    data: {
                      display_name: "LocalLLaMA",
                      title: "LocalLLaMA",
                      public_description: "Local LLM discussion",
                      url: "/r/LocalLLaMA/",
                    },
                  },
                ],
              },
            };
    return new Response(JSON.stringify(json), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;

  try {
    const result = await executeRedditTask({
      task_id: "reddit-bootstrap",
      type: "bootstrap_events",
      max_items_per_scope: 3,
    });

    assert.equal(result.status, "ok");
    assert.equal(result.scope_counts.reddit_saved, 1);
    assert.equal(result.scope_counts.reddit_upvoted, 1);
    assert.equal(result.scope_counts.reddit_subscribed, 1);
    assert.deepEqual(
      result.items.map((item) => item.scope),
      ["reddit_saved", "reddit_upvoted", "reddit_subscribed"],
    );
    assert.equal(result.items[2]?.content_type, "subreddit");
    assert.ok(calls.some((url) => url.includes("/user/agent_user/saved.json")));
    assert.ok(calls.some((url) => url.includes("/user/agent_user/upvoted.json")));
    assert.ok(calls.some((url) => url.includes("/subreddits/mine/subscriber.json")));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("executeRedditTask fails instead of hanging when Reddit JSON fetch times out", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (_input: string | URL | Request, init?: RequestInit) => {
    return await new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal;
      if (!signal) return;
      if (signal.aborted) {
        reject(new Error("aborted"));
        return;
      }
      signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
    });
  }) as typeof fetch;

  try {
    const result = await Promise.race([
      executeRedditTask({
        task_id: "reddit-timeout",
        type: "bootstrap_events",
        fetch_timeout_ms: 10,
      }),
      new Promise<"timed-out">((resolve) => setTimeout(() => resolve("timed-out"), 200)),
    ]);

    assert.notEqual(result, "timed-out");
    if (result !== "timed-out") {
      assert.equal(result.status, "failed");
      assert.equal(result.error, "reddit_task_failed");
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("installRedditMessageListener responds after sending task result", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const originalFetch = globalThis.fetch;
  delete (globalThis as Record<string, unknown>).__OPENBILICLAW_REDDIT_TASK_LISTENER__;

  let listener:
    | ((
        message: unknown,
        sender: unknown,
        sendResponse: (response: unknown) => void,
      ) => boolean)
    | null = null;
  const sentMessages: unknown[] = [];
  (globalThis as { chrome?: unknown }).chrome = {
    storage: {
      local: {
        get(_key: string, callback: (items: Record<string, unknown>) => void) {
          callback({});
        },
      },
      onChanged: {
        addListener() {
          // no-op
        },
      },
    },
    runtime: {
      onMessage: {
        addListener(callback: typeof listener) {
          listener = callback;
        },
      },
      async sendMessage(message: unknown) {
        sentMessages.push(message);
        return {};
      },
    },
  };
  globalThis.fetch = (async (input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("/sources/_debug/log")) {
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }
    return new Response(
      JSON.stringify({
        data: {
          children: [
            {
              kind: "t3",
              data: {
                id: "hot1",
                title: "Hot Reddit item",
                permalink: "/r/test/comments/hot1/title/",
              },
            },
          ],
        },
      }),
      { status: 200 },
    );
  }) as typeof fetch;

  try {
    installRedditMessageListener();
    assert.ok(listener);
    let response: unknown = null;
    const keepChannelOpen = listener(
      {
        action: "REDDIT_TASK_EXECUTE",
        data: { task_id: "listener-task", type: "hot", max_items: 1 },
      },
      {},
      (value) => {
        response = value;
      },
    );
    assert.equal(keepChannelOpen, true);
    await new Promise((resolve) => setTimeout(resolve, 20));

    assert.deepEqual(response, { ok: true });
    assert.equal(sentMessages.length, 1);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    globalThis.fetch = originalFetch;
    delete (globalThis as Record<string, unknown>).__OPENBILICLAW_REDDIT_TASK_LISTENER__;
  }
});
