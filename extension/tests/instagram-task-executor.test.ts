import assert from "node:assert/strict";
import test from "node:test";

import {
  buildInstagramDiscoverResult,
  classifyInstagramApiEnvelope,
  executeInstagramBootstrap,
  explicitInstagramDiscoverEmpty,
  InstagramTaskSingleflight,
  instagramPaginatedUrl,
  normalizeInstagramFollowingUser,
  normalizeInstagramMedia,
  type InstagramBootstrapTask,
} from "../src/content/instagram/task-executor.ts";

function jsonResponse(value: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });
}

const baseTask: InstagramBootstrapTask = {
  id: "instagram-task-1",
  type: "bootstrap_events",
  claim_token: "claim-token",
  scopes: ["instagram_liked"],
  max_items_per_scope: 300,
  max_pages_per_scope: 3,
  request_interval_ms: 1_000,
};

test("normalizes media and followed users without fabricating engagement", () => {
  const media = normalizeInstagramMedia({
    pk: "123",
    code: "ABC_123",
    media_type: 2,
    product_type: "clips",
    caption: { text: "A useful reel" },
    user: { pk: "88", username: "creator" },
    taken_at: 1_700_000_000,
    image_versions2: { candidates: [{ url: "https://scontent.example/cover.jpg" }] },
    like_count: 9_999,
  }, "instagram_liked");
  assert.deepEqual(media, {
    scope: "instagram_liked",
    id: "123",
    code: "ABC_123",
    content_type: "reel",
    url: "https://www.instagram.com/reel/ABC_123/",
    title: "A useful reel",
    description: "A useful reel",
    cover_url: "https://scontent.example/cover.jpg",
    author_id: "88",
    author_name: "creator",
    published_at: "2023-11-14T22:13:20.000Z",
  });
  assert.equal("like_count" in (media || {}), false);

  assert.deepEqual(normalizeInstagramFollowingUser({
    pk: 99,
    username: "openai",
    full_name: "OpenAI",
    profile_pic_url: "https://example.test/avatar.jpg",
  }), {
    scope: "instagram_following",
    id: "99",
    content_type: "user",
    url: "https://www.instagram.com/openai/",
    author_id: "99",
    author_name: "openai",
    title: "OpenAI",
    cover_url: "https://example.test/avatar.jpg",
  });
});

test("first private-page request omits max_id and later requests preserve opaque cursor", () => {
  assert.equal(
    new URL(instagramPaginatedUrl("/api/v1/feed/liked/")).searchParams.has("max_id"),
    false,
  );
  assert.equal(
    new URL(instagramPaginatedUrl("/api/v1/feed/liked/", "opaque+/cursor=="))
      .searchParams.get("max_id"),
    "opaque+/cursor==",
  );
});

test("classifies auth, challenge, HTML and throttling fail-closed", () => {
  assert.deepEqual(
    classifyInstagramApiEnvelope(200, "application/json", {
      status: "fail",
      message: "challenge_required",
    }),
    { kind: "challenge", error: "challenge_required" },
  );
  assert.equal(classifyInstagramApiEnvelope(429, "application/json", {}).kind, "rate_limited");
  assert.equal(classifyInstagramApiEnvelope(200, "text/html", {}).kind, "html_response");
  assert.equal(classifyInstagramApiEnvelope(401, "text/html", "<html>login</html>").kind, "login_required");
  assert.equal(classifyInstagramApiEnvelope(200, "text/html", "<html>checkpoint required</html>").kind, "challenge");
  assert.equal(classifyInstagramApiEnvelope(403, "text/html", "<html>checkpoint required</html>").kind, "challenge");
  assert.equal(classifyInstagramApiEnvelope(200, "text/html", "<html>Please wait a few minutes</html>").kind, "rate_limited");
  assert.equal(classifyInstagramApiEnvelope(403, "application/json", {
    status: "fail",
    message: "challenge_required",
  }).kind, "challenge");
  assert.equal(classifyInstagramApiEnvelope(403, "application/json", {}).kind, "login_required");
  assert.equal(classifyInstagramApiEnvelope(200, "application/json", {
    status: "fail",
    message: "login_required",
  }).kind, "login_required");
});

test("task singleflight deduplicates recovery resends and rejects a different claim", async () => {
  const singleflight = new InstagramTaskSingleflight();
  const task = {
    id: "discover-1",
    type: "discover" as const,
    claim_token: "claim-1",
    mode: "topic" as const,
    query: "ai",
    max_items: 30,
    max_pages: 3,
  };
  let executions = 0;
  const executor = async () => {
    executions += 1;
    await Promise.resolve();
    return {
      task_id: task.id,
      claim_token: task.claim_token,
      status: "empty" as const,
      items: [],
      scope_counts: { discover: 0 },
      scope_complete: { discover: true },
    };
  };
  const [first, resent] = await Promise.all([
    singleflight.run(task, executor),
    singleflight.run(task, executor),
  ]);
  assert.equal(executions, 1);
  assert.deepEqual(resent, first);
  assert.deepEqual(await singleflight.run(task, executor), first);
  const conflict = await singleflight.run({ ...task, claim_token: "claim-2" }, executor);
  assert.equal(conflict.status, "failed");
  assert.equal(conflict.error, "task_claim_conflict");
  assert.equal(executions, 1);
});

test("unavailable/error pages are never affirmative discover-empty", () => {
  assert.equal(explicitInstagramDiscoverEmpty("No posts yet"), true);
  assert.equal(explicitInstagramDiscoverEmpty("没有帖子"), true);
  assert.equal(explicitInstagramDiscoverEmpty("Page isn't available"), false);
  assert.equal(explicitInstagramDiscoverEmpty("页面无法显示"), false);
  assert.equal(explicitInstagramDiscoverEmpty("A shell that happens to mention no posts yet here"), false);
});

test("bootstrap requires numeric identity and returns terminal evidence for valid empty", async () => {
  const urls: string[] = [];
  const result = await executeInstagramBootstrap(baseTask, async (input) => {
    const url = String(input);
    urls.push(url);
    if (url.includes("accounts/current_user")) {
      return jsonResponse({ user: { pk: "42", username: "private-name" }, status: "ok" });
    }
    return jsonResponse({ items: [], more_available: false, status: "ok" });
  });

  assert.equal(result.status, "empty");
  assert.equal(result.account_id, "42");
  assert.equal(result.scope_complete.instagram_liked, true);
  assert.equal(result.debug?.response_observed, true);
  assert.equal(result.debug?.terminal_evidence, "instagram_liked");
  assert.equal("account_username" in (result.debug || {}), false);
  assert.equal(new URL(urls[1]).searchParams.has("max_id"), false);
});

test("bootstrap requires durable account-id progress, not the pre-identity heartbeat", async () => {
  const originalChrome = globalThis.chrome;
  try {
    globalThis.chrome = {
      runtime: {
        sendMessage: async (message: { data?: { accepted?: boolean } }) => ({
          ok: message.data?.accepted === true,
        }),
      },
    } as unknown as typeof chrome;
    const urls: string[] = [];
    const result = await executeInstagramBootstrap(baseTask, async (input) => {
      const url = String(input);
      urls.push(url);
      if (url.includes("accounts/current_user")) {
        return jsonResponse({ user: { pk: "42" }, status: "ok" });
      }
      return jsonResponse({ items: [], more_available: false, status: "ok" });
    });
    assert.equal(result.status, "empty");
    assert.equal(urls.length, 2, "identity + lane proceed after account-id progress ACK");

    globalThis.chrome = {
      runtime: {
        sendMessage: async (message: { data?: { accepted?: boolean } }) => ({
          ok: message.data?.accepted !== true,
        }),
      },
    } as unknown as typeof chrome;
    const blockedUrls: string[] = [];
    const blocked = await executeInstagramBootstrap(baseTask, async (input) => {
      const url = String(input);
      blockedUrls.push(url);
      return jsonResponse({ user: { pk: "42" }, status: "ok" });
    });
    assert.equal(blocked.status, "failed");
    assert.equal(blocked.error, "progress_persistence_unavailable");
    assert.equal(blockedUrls.length, 1, "no personal lane GET before account-id progress is durable");
  } finally {
    globalThis.chrome = originalChrome;
  }
});

test("challenge and oversized identity responses cannot become empty", async () => {
  const challenge = await executeInstagramBootstrap(baseTask, async () =>
    jsonResponse({ status: "fail", message: "challenge_required" }));
  assert.equal(challenge.status, "failed");
  assert.equal(challenge.error, "challenge_required");
  assert.deepEqual(challenge.scope_complete, { instagram_liked: false });

  const oversized = await executeInstagramBootstrap(baseTask, async () =>
    jsonResponse({ user: { pk: "42" } }, 200, { "content-length": String(2 * 1024 * 1024 + 1) }));
  assert.equal(oversized.status, "failed");
  assert.equal(oversized.error, "response_too_large");
});

test("bootstrap follows opaque cursor on page two and respects request pacing", async () => {
  const urls: string[] = [];
  const sleeps: number[] = [];
  let lanePage = 0;
  const result = await executeInstagramBootstrap(baseTask, async (input) => {
    const url = String(input);
    urls.push(url);
    if (url.includes("accounts/current_user")) {
      return jsonResponse({ user: { pk: "42" }, status: "ok" });
    }
    lanePage += 1;
    if (lanePage === 1) {
      return jsonResponse({
        items: [{ pk: "101", code: "FIRST", media_type: 1 }],
        more_available: true,
        next_max_id: "opaque+/cursor==",
        status: "ok",
      });
    }
    return jsonResponse({
      items: [{ pk: "102", code: "SECOND", media_type: 1 }],
      more_available: false,
      status: "ok",
    });
  }, async (milliseconds) => { sleeps.push(milliseconds); });

  assert.equal(new URL(urls[1]).searchParams.has("max_id"), false);
  assert.equal(new URL(urls[2]).searchParams.get("max_id"), "opaque+/cursor==");
  assert.deepEqual(sleeps, [1_000]);
  assert.equal(result.status, "ok");
  assert.deepEqual(result.items.map((item) => item.id), ["101", "102"]);
  assert.equal(result.scope_complete.instagram_liked, true);
});

test("bootstrap retains accepted rows when the next page is rate limited or challenged", async () => {
  for (const secondPage of [
    () => jsonResponse({}, 429),
    () => jsonResponse({ status: "fail", message: "challenge_required" }),
  ]) {
    let lanePage = 0;
    const result = await executeInstagramBootstrap(baseTask, async (input) => {
      if (String(input).includes("accounts/current_user")) {
        return jsonResponse({ user: { pk: "42" }, status: "ok" });
      }
      lanePage += 1;
      if (lanePage === 1) {
        return jsonResponse({
          items: [{ pk: "101", code: "FIRST", media_type: 1 }],
          more_available: true,
          next_max_id: "next",
          status: "ok",
        });
      }
      return secondPage();
    }, async () => {});
    assert.equal(result.status, "partial");
    assert.deepEqual(result.items.map((item) => item.id), ["101"]);
    assert.equal(result.scope_complete.instagram_liked, false);
    assert.equal(result.debug?.response_observed, true);
    assert.ok(String(result.debug?.terminal_evidence));
  }
});

test("challenge or 429 safety-fuse stops every remaining bootstrap lane", async () => {
  for (const failure of [
    () => jsonResponse({ status: "fail", message: "challenge_required" }),
    () => jsonResponse({}, 429),
  ]) {
    const urls: string[] = [];
    const result = await executeInstagramBootstrap({
      ...baseTask,
      scopes: ["instagram_liked", "instagram_saved", "instagram_following"],
    }, async (input) => {
      const url = String(input);
      urls.push(url);
      if (url.includes("accounts/current_user")) {
        return jsonResponse({ user: { pk: "42" }, status: "ok" });
      }
      return failure();
    }, async () => {});
    assert.equal(urls.length, 2, "identity + first lane only");
    assert.equal(urls.some((url) => url.includes("saved/posts")), false);
    assert.equal(urls.some((url) => url.includes("friendships/")), false);
    assert.equal(result.status, "failed");
    assert.deepEqual(result.scope_counts, {
      instagram_liked: 0,
      instagram_saved: 0,
      instagram_following: 0,
    });
    assert.deepEqual(result.scope_complete, {
      instagram_liked: false,
      instagram_saved: false,
      instagram_following: false,
    });
  }
});

test("hitting a frozen item cap is partial even when the page also says terminal", async () => {
  const result = await executeInstagramBootstrap({
    ...baseTask,
    max_items_per_scope: 1,
  }, async (input) => {
    if (String(input).includes("accounts/current_user")) {
      return jsonResponse({ user: { pk: "42" }, status: "ok" });
    }
    return jsonResponse({
      items: [{ pk: "101", code: "ONLY", media_type: 1 }],
      more_available: false,
      status: "ok",
    });
  }, async () => {});
  assert.equal(result.status, "partial");
  assert.equal(result.scope_complete.instagram_liked, false);
  assert.equal(result.error, "instagram_liked:item_cap_reached");
  assert.deepEqual(result.items.map((item) => item.id), ["101"]);
});

test("discover result requires an observed envelope before declaring empty", () => {
  const task = {
    id: "discover-1",
    type: "discover" as const,
    claim_token: "claim",
    mode: "topic" as const,
    query: "ai",
    max_items: 30,
    max_pages: 3,
  };
  const terminal = buildInstagramDiscoverResult(task, [{
    route: "topic",
    items: [],
    has_next_page: false,
    affirmative_terminal: true,
  }]);
  assert.equal(terminal.status, "empty");
  assert.equal(terminal.scope_complete.discover, true);
  assert.equal(terminal.debug?.response_observed, true);

  const partial = buildInstagramDiscoverResult(task, [{
    route: "topic",
    items: [{ id: "1", code: "ONE", content_type: "post", url: "https://www.instagram.com/p/ONE/" }],
    cursor: "next",
    has_next_page: true,
  }]);
  assert.equal(partial.status, "partial");
  assert.equal(partial.scope_complete.discover, false);
  assert.deepEqual(partial.items.map((item) => item.id), ["1"]);

  const falseEmpty = buildInstagramDiscoverResult(task, [], true);
  assert.equal(falseEmpty.status, "failed");
  assert.equal(falseEmpty.scope_complete.discover, false);
  assert.equal(falseEmpty.debug?.response_observed, false);

  const unrelatedEnvelope = buildInstagramDiscoverResult(task, [{
    route: "creator",
    items: [],
    has_next_page: false,
  }], true);
  assert.equal(unrelatedEnvelope.status, "failed");
  assert.equal(unrelatedEnvelope.debug?.response_observed, false);

  const creator = buildInstagramDiscoverResult({
    ...task,
    mode: "creator",
    query: undefined,
    username: "private-name",
  }, [{ route: "creator", items: [], has_next_page: false }]);
  assert.equal("username" in (creator.debug || {}), false);
});

test("topic discovery waits for every observed collection and degrades rejected schemas", () => {
  const task = {
    id: "discover-mixed",
    type: "discover" as const,
    claim_token: "claim",
    mode: "topic" as const,
    query: "ai",
    max_items: 30,
    max_pages: 3,
  };
  const mixed = buildInstagramDiscoverResult(task, [
    {
      route: "topic",
      collection_id: "topic:top",
      items: [{ id: "1", code: "ONE", content_type: "post", url: "https://www.instagram.com/p/ONE/" }],
      observed_count: 1,
      rejected_count: 0,
      shape_valid: true,
      has_next_page: false,
    },
    {
      route: "topic",
      collection_id: "topic:recent",
      items: [{ id: "2", code: "TWO", content_type: "post", url: "https://www.instagram.com/p/TWO/" }],
      observed_count: 1,
      rejected_count: 0,
      shape_valid: true,
      has_next_page: true,
    },
  ]);
  assert.equal(mixed.status, "partial");
  assert.equal(mixed.scope_complete.discover, false);
  assert.equal(mixed.debug?.terminal_collection_count, 1);

  const rejected = buildInstagramDiscoverResult(task, [{
    route: "topic",
    collection_id: "topic:recent",
    items: [],
    observed_count: 2,
    rejected_count: 2,
    shape_valid: false,
    has_next_page: false,
  }]);
  assert.equal(rejected.status, "failed");
  assert.equal(rejected.error, "response_rows_rejected");
  assert.equal(rejected.scope_complete.discover, false);
});
