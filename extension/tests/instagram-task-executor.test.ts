import assert from "node:assert/strict";
import test from "node:test";

import {
  buildInstagramDiscoverResult,
  classifyInstagramApiEnvelope,
  classifyInstagramDiscoverPage,
  executeInstagramBootstrap,
  explicitInstagramDiscoverEmpty,
  InstagramTaskSingleflight,
  instagramPaginatedUrl,
  normalizeInstagramFollowingUser,
  normalizeInstagramMedia,
  type InstagramBootstrapTask,
} from "../src/content/instagram/task-executor.ts";

test("real public-page soft 404 is distinct from empty, login and challenge", () => {
  // Read-only Chrome sample, 2026-09-26: /popular/anime/ returns HTTP 200.
  assert.equal(classifyInstagramDiscoverPage(
    "/popular/anime/", "Page无法访问 • Instagram",
    "登录\nPage无法访问\n链接可能已损坏或主页被移除。\n注册 Instagram",
  ), "public_page_unavailable");
  assert.equal(classifyInstagramDiscoverPage(
    "/popular/music/", "Music • Instagram", "登录\n注册\nMusic\n页面无法访问教程",
  ), "");
  assert.equal(classifyInstagramDiscoverPage("/popular/music/", "Instagram", ""), "");
  assert.equal(classifyInstagramDiscoverPage(
    "/challenge/", "Page无法访问 • Instagram", "链接可能已损坏或主页被移除。",
  ), "challenge_required");
  assert.equal(classifyInstagramDiscoverPage(
    "/accounts/login/", "Instagram", "登录",
  ), "login_required");
});

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
  scopes: ["instagram_saved"],
  max_items_per_scope: 300,
  max_pages_per_scope: 3,
  request_interval_ms: 1_000,
};

function identityResponse(url: string): Response | null {
  if (url.includes("accounts/edit/web_form_data")) {
    return jsonResponse({ status: "ok", form_data: { username: "fixture" } });
  }
  if (url === "https://www.instagram.com/") {
    return new Response('<script data-sjs>{"__bbox":{"define":[["PolarisViewer",[],{"id":"42","data":{"id":"42","username":"fixture"}},1]]}}</script>',
      { headers: { "content-type": "text/html" } });
  }
  return null;
}

test("legal 30-second pacing does not spend the 20-second network timeout", async (t) => {
  t.mock.timers.enable({ apis: ["Date", "setTimeout"], now: 0 });
  const started: { url: string; at: number; aborted: boolean }[] = [];
  const result = await executeInstagramBootstrap({ ...baseTask, request_interval_ms: 30_000 },
    async (input, init) => {
      started.push({ url: String(input), at: Date.now(), aborted: !!init?.signal?.aborted });
      if (init?.signal?.aborted) throw new DOMException("Aborted", "AbortError");
      return identityResponse(String(input)) || jsonResponse({ items: [], more_available: false });
    }, async ms => { t.mock.timers.tick(ms); });
  assert.equal(result.status, "empty");
  assert.ok(started.some(row => row.url.includes("/feed/saved/posts/")));
  assert.ok(started.every(row => !row.aborted));
  assert.ok(started.slice(1).every((row, i) => row.at - started[i].at >= 30_000));
});

test("pacing fix still aborts an actual request exceeding 20 seconds", async (t) => {
  t.mock.timers.enable({ apis: ["Date", "setTimeout"], now: 0 });
  let attempts = 0;
  const result = await executeInstagramBootstrap({ ...baseTask, request_interval_ms: 30_000 },
    async (_input, init) => {
      attempts += 1;
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
        t.mock.timers.tick(20_001);
      });
    }, async ms => { t.mock.timers.tick(ms); });
  assert.equal(result.status, "failed");
  assert.equal(result.error, "request_timeout");
  assert.equal(attempts, 1);
});

for (const scope of ["instagram_saved", "instagram_following"] as const) {
  for (const mixed of [false, true]) {
    test(`${scope} rejects schema drift without asserting a complete empty snapshot (mixed: ${mixed})`, async () => {
      const good = scope === "instagram_saved"
        ? { media: { pk: "1001", code: "FIXTURE", media_type: 1 } }
        : { pk: "1001", username: "fixture" };
      const rows = [...(mixed ? [good] : []), { renamed_id: "2002" }];
      const result = await executeInstagramBootstrap({ ...baseTask, scopes: [scope] },
        async input => identityResponse(String(input)) || jsonResponse({
          [scope === "instagram_saved" ? "items" : "users"]: rows, more_available: false,
        }), async () => {});
      assert.equal(result.status, mixed ? "partial" : "failed");
      assert.equal(result.items.length, mixed ? 1 : 0);
      assert.equal(result.scope_complete[scope], false);
      assert.match(result.error || "", /response_(rows_rejected|schema_degraded)/);
    });
  }
}

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
  }, "instagram_saved");
  assert.deepEqual(media, {
    scope: "instagram_saved",
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
  assert.equal(classifyInstagramApiEnvelope(
    200,
    "text/html",
    '<form action="/accounts/login/"><input name="username" required></form>',
  ).kind, "login_required");
  assert.equal(classifyInstagramApiEnvelope(200, "text/html", "<html>challenge_required</html>").kind, "challenge");
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

test("identity redirected to homepage HTML is not a challenge from bundled script names", async () => {
  // Redacted/minimized 2026-09-26 browser response: current_user redirected to
  // an ordinary Instagram homepage, with challenge only in script metadata.
  const html = '<html><head><title>Instagram</title><script>{"routes":["challenge","checkpoint","captcha","login"]}</script></head><body><main>Instagram</main></body></html>';
  const result = await executeInstagramBootstrap(baseTask, async () =>
    new Response(html, { status: 200, headers: { "content-type": "text/html; charset=utf-8" } }));
  assert.equal(result.status, "failed");
  assert.equal(result.error, "html_response");
  assert.deepEqual(result.items, []);
  assert.deepEqual(result.scope_complete, { instagram_saved: false });
});

test("generic HTML metadata is not authoritative login or challenge evidence", () => {
  const html = '<html><head><meta content="Log in to Instagram"><link href="/static/challenge.css"></head><body><main>Instagram</main></body></html>';
  assert.equal(classifyInstagramApiEnvelope(200, "text/html", html).kind, "html_response");
});

test("a nullable checkpoint field is not a challenge instruction", () => {
  assert.equal(classifyInstagramApiEnvelope(400, "application/json", {
    status: "fail", message: "invalid_request", checkpoint_url: null,
  }).kind, "http_error");
});

test("an API redirect to a real challenge route still stops bootstrap without a visible message", async () => {
  let requests = 0;
  const result = await executeInstagramBootstrap(baseTask, async () => {
    requests += 1;
    const response = new Response('<html><script>boot()</script></html>', {
      status: 200, headers: { "content-type": "text/html" },
    });
    Object.defineProperty(response, "url", { value: "https://www.instagram.com/challenge/" });
    return response;
  });
  assert.equal(result.status, "failed");
  assert.equal(result.error, "challenge_required");
  assert.equal(requests, 1);
  assert.deepEqual(result.items, []);
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
    const identity = identityResponse(url);
    if (identity) return identity;
    return jsonResponse({ items: [], more_available: false, status: "ok" });
  }, async () => {});

  assert.equal(result.status, "empty");
  assert.equal(result.account_id, "42");
  assert.equal(result.scope_complete.instagram_saved, true);
  assert.equal(result.debug?.response_observed, true);
  assert.equal(result.debug?.terminal_evidence, "instagram_saved");
  assert.equal("account_username" in (result.debug || {}), false);
  assert.equal(new URL(urls[2]).searchParams.has("max_id"), false);
});

test("bootstrap paces every authoritative identity and private-page request", async () => {
  const urls: string[] = [];
  const waits: number[] = [];
  const result = await executeInstagramBootstrap(baseTask, async (input) => {
    const url = String(input);
    urls.push(url);
    const identity = identityResponse(url);
    if (identity) return identity;
    return jsonResponse({ items: [], more_available: false, status: "ok" });
  }, async (milliseconds) => {
    waits.push(milliseconds);
  });

  assert.equal(result.status, "empty");
  assert.equal(urls.length, 6);
  assert.equal(waits.length, urls.length - 1);
  assert.equal(waits.every(wait => wait > 0 && wait <= 1_000), true);
});

test("bootstrap discards the whole snapshot when the account changes during a private page", async () => {
  let currentAccount = { id: "42", username: "first" };
  const result = await executeInstagramBootstrap(baseTask, async (input) => {
    const url = String(input);
    if (url.endsWith("web_form_data/")) {
      return jsonResponse({
        status: "ok",
        form_data: { username: currentAccount.username },
      });
    }
    if (url === "https://www.instagram.com/") {
      const { id, username } = currentAccount;
      return new Response(
        `<script data-sjs>{"__bbox":{"define":[["PolarisViewer",[],{"id":"${id}","data":{"id":"${id}","username":"${username}"}},1]]}}</script>`,
        { headers: { "content-type": "text/html" } },
      );
    }
    currentAccount = { id: "84", username: "second" };
    return jsonResponse({
      items: [{ pk: "101", code: "FIRST", media_type: 1 }],
      more_available: false,
      status: "ok",
    });
  }, async () => {});

  assert.equal(result.status, "failed");
  assert.equal(result.error, "instagram_account_changed");
  assert.equal(result.account_id, "84");
  assert.deepEqual(result.items, []);
  assert.deepEqual(result.scope_counts, { instagram_saved: 0 });
  assert.deepEqual(result.scope_complete, { instagram_saved: false });
});

test("bootstrap detects an account switch before requesting the next private page", async () => {
  const originalChrome = globalThis.chrome;
  let currentAccount = { id: "42", username: "first" };
  let privateRequests = 0;
  try {
    globalThis.chrome = {
      runtime: {
        sendMessage: async (message: { data?: { phase?: string } }) => {
          if (message.data?.phase === "bootstrap") {
            currentAccount = { id: "84", username: "second" };
          }
          return { ok: true };
        },
      },
    } as unknown as typeof chrome;
    const result = await executeInstagramBootstrap(baseTask, async (input) => {
      const url = String(input);
      if (url.endsWith("web_form_data/")) {
        return jsonResponse({
          status: "ok",
          form_data: { username: currentAccount.username },
        });
      }
      if (url === "https://www.instagram.com/") {
        const { id, username } = currentAccount;
        return new Response(
          `<script data-sjs>{"__bbox":{"define":[["PolarisViewer",[],{"id":"${id}","data":{"id":"${id}","username":"${username}"}},1]]}}</script>`,
          { headers: { "content-type": "text/html" } },
        );
      }
      privateRequests += 1;
      return jsonResponse({
        items: [{ pk: "101", code: "FIRST", media_type: 1 }],
        more_available: true,
        next_max_id: "next",
        status: "ok",
      });
    }, async () => {});

    assert.equal(result.status, "failed");
    assert.equal(result.error, "instagram_account_changed");
    assert.equal(result.account_id, "84");
    assert.deepEqual(result.items, []);
    assert.equal(privateRequests, 1, "the switched account must not receive page two");
  } finally {
    globalThis.chrome = originalChrome;
  }
});

test("bootstrap treats a different viewer ID with the same username as an account switch", async () => {
  const originalChrome = globalThis.chrome;
  let currentAccountId = "42";
  let identityFormRequests = 0;
  let privateRequests = 0;
  try {
    globalThis.chrome = {
      runtime: {
        sendMessage: async (message: { data?: { phase?: string } }) => {
          if (message.data?.phase === "bootstrap") currentAccountId = "84";
          return { ok: true };
        },
      },
    } as unknown as typeof chrome;
    const result = await executeInstagramBootstrap(baseTask, async (input) => {
      const url = String(input);
      if (url.endsWith("web_form_data/")) {
        identityFormRequests += 1;
        return jsonResponse({ status: "ok", form_data: { username: "unchanged" } });
      }
      if (url === "https://www.instagram.com/") {
        return new Response(
          `<script data-sjs>{"__bbox":{"define":[["PolarisViewer",[],{"id":"${currentAccountId}","data":{"id":"${currentAccountId}","username":"unchanged"}},1]]}}</script>`,
          { headers: { "content-type": "text/html" } },
        );
      }
      privateRequests += 1;
      return jsonResponse({
        items: [{ pk: "101", code: "FIRST", media_type: 1 }],
        more_available: true,
        next_max_id: "next",
        status: "ok",
      });
    }, async () => {});

    assert.equal(result.status, "failed");
    assert.equal(result.error, "instagram_account_changed");
    assert.equal(result.account_id, "84");
    assert.deepEqual(result.items, []);
    assert.deepEqual(result.scope_counts, { instagram_saved: 0 });
    assert.equal(identityFormRequests, 1, "a conclusive viewer mismatch needs no fallback pair");
    assert.equal(privateRequests, 1, "the switched account must not receive page two");
  } finally {
    globalThis.chrome = originalChrome;
  }
});

test("bootstrap final identity check discards already persisted rows after an account switch", async () => {
  const originalChrome = globalThis.chrome;
  let currentAccount = { id: "42", username: "first" };
  try {
    globalThis.chrome = {
      runtime: {
        sendMessage: async (message: { data?: { phase?: string } }) => {
          if (message.data?.phase === "bootstrap") {
            currentAccount = { id: "84", username: "second" };
          }
          return { ok: true };
        },
      },
    } as unknown as typeof chrome;
    const result = await executeInstagramBootstrap(baseTask, async (input) => {
      const url = String(input);
      if (url.endsWith("web_form_data/")) {
        return jsonResponse({
          status: "ok",
          form_data: { username: currentAccount.username },
        });
      }
      if (url === "https://www.instagram.com/") {
        const { id, username } = currentAccount;
        return new Response(
          `<script data-sjs>{"__bbox":{"define":[["PolarisViewer",[],{"id":"${id}","data":{"id":"${id}","username":"${username}"}},1]]}}</script>`,
          { headers: { "content-type": "text/html" } },
        );
      }
      return jsonResponse({
        items: [{ pk: "101", code: "FIRST", media_type: 1 }],
        more_available: false,
        status: "ok",
      });
    }, async () => {});

    assert.equal(result.status, "failed");
    assert.equal(result.error, "instagram_account_changed");
    assert.equal(result.account_id, "84");
    assert.deepEqual(result.items, []);
    assert.deepEqual(result.scope_counts, { instagram_saved: 0 });
  } finally {
    globalThis.chrome = originalChrome;
  }
});

test("anonymous or conflicting fresh viewer prevents all personal lane requests", async () => {
  for (const viewer of [null, { id: "42", data: { id: "43", username: "fixture" } }]) {
    const urls: string[] = [];
    const result = await executeInstagramBootstrap(baseTask, async (input) => {
      const url = String(input);
      urls.push(url);
      if (url.endsWith("web_form_data/")) return identityResponse(url)!;
      return new Response(`<script data-sjs>{"__bbox":{"define":[["PolarisViewer",[],${JSON.stringify(viewer)},1]]}}</script>`,
        { headers: { "content-type": "text/html" } });
    }, async () => {});
    assert.equal(result.status, "failed");
    assert.equal(result.error, "account_identity_missing");
    assert.equal(urls.length, 2);
    assert.equal(urls.some(url => url.includes("/feed/")), false);
  }
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
      const identity = identityResponse(url);
      if (identity) return identity;
      return jsonResponse({ items: [], more_available: false, status: "ok" });
    }, async () => {});
    assert.equal(result.status, "empty");
    assert.equal(
      urls.length,
      6,
      "initial form/viewer + before/after/final viewer checks + one lane request",
    );

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
      return identityResponse(url)!;
    }, async () => {});
    assert.equal(blocked.status, "failed");
    assert.equal(blocked.error, "progress_persistence_unavailable");
    assert.equal(blockedUrls.length, 2, "no personal lane GET before account-id progress is durable");
  } finally {
    globalThis.chrome = originalChrome;
  }
});

test("challenge and oversized identity responses cannot become empty", async () => {
  const challenge = await executeInstagramBootstrap(baseTask, async () =>
    jsonResponse({ status: "fail", message: "challenge_required" }));
  assert.equal(challenge.status, "failed");
  assert.equal(challenge.error, "challenge_required");
  assert.deepEqual(challenge.scope_complete, { instagram_saved: false });

  const oversized = await executeInstagramBootstrap(baseTask, async () =>
    jsonResponse({ user: { pk: "42" } }, 200, { "content-length": String(2 * 1024 * 1024 + 1) }));
  assert.equal(oversized.status, "failed");
  assert.equal(oversized.error, "response_too_large");
});

test("authoritative identity recheck failure stops without a fallback identity retry", async () => {
  const urls: string[] = [];
  const result = await executeInstagramBootstrap(baseTask, async (input) => {
    const url = String(input);
    urls.push(url);
    if (urls.length <= 2) return identityResponse(url)!;
    return jsonResponse({}, 429);
  }, async () => {});

  assert.equal(result.status, "failed");
  assert.equal(result.error, "rate_limited");
  assert.deepEqual(result.items, []);
  assert.equal(urls.length, 3, "no full-pair retry or private request after authoritative 429");
});

test("bootstrap follows opaque cursor on page two and respects request pacing", async () => {
  const urls: string[] = [];
  const sleeps: number[] = [];
  let lanePage = 0;
  const result = await executeInstagramBootstrap(baseTask, async (input) => {
    const url = String(input);
    urls.push(url);
    const identity = identityResponse(url);
    if (identity) return identity;
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

  const laneUrls = urls.filter((url) => url.includes("/api/v1/feed/saved/posts/"));
  assert.equal(new URL(laneUrls[0]!).searchParams.has("max_id"), false);
  assert.equal(new URL(laneUrls[1]!).searchParams.get("max_id"), "opaque+/cursor==");
  assert.equal(sleeps.length > 0, true);
  assert.equal(sleeps.every(wait => wait > 0 && wait <= 1_000), true);
  assert.equal(result.status, "ok");
  assert.deepEqual(result.items.map((item) => item.id), ["101", "102"]);
  assert.equal(result.scope_complete.instagram_saved, true);
});

test("bootstrap retains accepted rows when the next page is rate limited or challenged", async () => {
  for (const secondPage of [
    () => jsonResponse({}, 429),
    () => jsonResponse({ status: "fail", message: "challenge_required" }),
  ]) {
    let lanePage = 0;
    const result = await executeInstagramBootstrap(baseTask, async (input) => {
      const identity = identityResponse(String(input));
      if (identity) return identity;
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
    assert.equal(result.scope_complete.instagram_saved, false);
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
      scopes: ["instagram_saved", "instagram_liked", "instagram_following"],
    }, async (input) => {
      const url = String(input);
      urls.push(url);
      const identity = identityResponse(url);
      if (identity) return identity;
      return failure();
    }, async () => {});
    assert.equal(
      urls.filter((url) => url.includes("/api/v1/feed/saved/posts/")).length,
      1,
      "only the first private lane is requested",
    );
    assert.equal(
      urls.length,
      4,
      "initial identity + pre-request viewer + authoritative failure; no post-failure probe",
    );
    assert.equal(urls.some((url) => url.includes("feed/liked")), false);
    assert.equal(urls.some((url) => url.includes("friendships/")), false);
    assert.equal(result.status, "failed");
    assert.deepEqual(result.scope_counts, {
      instagram_saved: 0,
      instagram_liked: 0,
      instagram_following: 0,
    });
    assert.deepEqual(result.scope_complete, {
      instagram_saved: false,
      instagram_liked: false,
      instagram_following: false,
    });
  }
});

test("hitting a frozen item cap is partial even when the page also says terminal", async () => {
  const result = await executeInstagramBootstrap({
    ...baseTask,
    max_items_per_scope: 1,
  }, async (input) => {
    const identity = identityResponse(String(input));
    if (identity) return identity;
    return jsonResponse({
      items: [{ pk: "101", code: "ONLY", media_type: 1 }],
      more_available: false,
      status: "ok",
    });
  }, async () => {});
  assert.equal(result.status, "partial");
  assert.equal(result.scope_complete.instagram_saved, false);
  assert.equal(result.error, "instagram_saved:item_cap_reached");
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
