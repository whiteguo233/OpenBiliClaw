import test from "node:test";
import assert from "node:assert/strict";

import {
  appendRecommendations,
  cacheConfigSnapshot,
  applyBackendUpdate,
  checkBackendStatus,
  checkBackendUpdate,
  discoverConfigModels,
  fetchPendingDelight,
  fetchPendingDelightBatch,
  fetchActivityFeed,
  fetchChatTurn,
  fetchChatTurns,
  fetchConfig,
  fetchHealth,
  fetchProfileSummary,
  fetchSourceShareSuggestion,
  fetchV2exIdentity,
  acceptV2exBrowserIdentity,
  fetchUpdateStatus,
  fetchWatchLater,
  probeConfigService,
  readCachedConfigSnapshot,
  requestJson,
  reshuffleRecommendations,
  respondToAvoidanceProbe,
  startInit,
  startChatTurn,
  streamChatTurn,
  updateConfig,
  __resetPopupHealthCacheForTests,
} from "../popup/popup-api.js";
import { __resetBackendEndpointForTests } from "../popup/popup-backend-config.js";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("V2EX identity helpers use the dedicated read and acceptance endpoints", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, async json() { return { status: "resolved", username: "alice" }; } };
  };

  await fetchV2exIdentity();
  await acceptV2exBrowserIdentity(" alice ");

  assert.match(calls[0].url, /\/api\/sources\/v2ex\/identity$/);
  assert.equal(calls[0].options.method, "GET");
  assert.match(calls[1].url, /\/api\/sources\/v2ex\/identity$/);
  assert.equal(calls[1].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[1].options.body), { username: "alice", accept: true });
});

test("guided init API calls all have finite request deadlines", () => {
  const source = readFileSync(resolve("popup/popup-api.js"), "utf8");
  assert.match(source, /requestJson\("\/init-status", \{ method: "GET", timeoutMs: 45000 \}\)/);
  assert.match(source, /body: JSON\.stringify\(payload\),\s+timeoutMs: 60000,/);
  assert.match(source, /requestJson\("\/init\/cancel", \{ method: "POST", timeoutMs: 15000 \}\)/);
});

test("discoverConfigModels sends a no-write exact-instance request", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { ok: true, instance_id: "relay-main", models: ["model-a"] };
      },
    };
  };

  const result = await discoverConfigModels(
    { llm: { routing_version: 2, instances: {} } },
    "relay-main",
  );

  assert.equal(result.models[0], "model-a");
  assert.match(calls[0].url, /\/api\/config\/discover-models$/);
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    instance_id: "relay-main",
    config: { llm: { routing_version: 2, instances: {} } },
  });
  assert.equal(calls[0].options.method, "POST");
});

test("startInit sends Bangumi username in scoped source_options", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, async json() { return { run_id: "run-1" }; } };
  };

  await startInit({ sources: ["bangumi"], bangumiUsername: " sai " });

  assert.deepEqual(JSON.parse(calls[0].options.body), {
    force: false,
    sources: ["bangumi"],
    source_options: { bangumi: { username: "sai" } },
  });
});

test("startInit omits source_options when no username is supplied (keep configured)", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, async json() { return { run_id: "run-1" }; } };
  };

  await startInit({ sources: ["bangumi"] });
  await startInit({ sources: ["bangumi"], bangumiUsername: null });

  // A missing/null username must not send source_options.bangumi.username, so
  // the backend keeps the configured value instead of erasing it.
  assert.deepEqual(JSON.parse(calls[0].options.body), { force: false, sources: ["bangumi"] });
  assert.deepEqual(JSON.parse(calls[1].options.body), { force: false, sources: ["bangumi"] });
});

test("startInit sends an empty username to clear the configured value", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, async json() { return { run_id: "run-1" }; } };
  };

  await startInit({ sources: ["bangumi"], bangumiUsername: "" });

  assert.deepEqual(JSON.parse(calls[0].options.body), {
    force: false,
    sources: ["bangumi"],
    source_options: { bangumi: { username: "" } },
  });
});

test("startInit sends a Bangumi access token in scoped source_options", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, async json() { return { run_id: "run-1" }; } };
  };

  await startInit({ sources: ["bangumi"], bangumiToken: "  tok-123  " });

  assert.deepEqual(JSON.parse(calls[0].options.body), {
    force: false,
    sources: ["bangumi"],
    source_options: { bangumi: { access_token: "tok-123" } },
  });
});

test("startInit sends both Bangumi username and access token when supplied", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, async json() { return { run_id: "run-1" }; } };
  };

  await startInit({ sources: ["bangumi"], bangumiUsername: "sai", bangumiToken: "tok-1" });

  assert.deepEqual(JSON.parse(calls[0].options.body), {
    force: false,
    sources: ["bangumi"],
    source_options: { bangumi: { username: "sai", access_token: "tok-1" } },
  });
});

test("startInit merges GitHub and Bangumi write-only source options", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, async json() { return { run_id: "run-1" }; } };
  };

  await startInit({
    sources: ["bangumi", "github"],
    bangumiUsername: "sai",
    githubUsername: " octocat ",
    githubToken: " github-pat ",
  });

  assert.deepEqual(JSON.parse(calls[0].options.body), {
    force: false,
    sources: ["bangumi", "github"],
    source_options: {
      bangumi: { username: "sai" },
      github: { username: "octocat", access_token: "github-pat" },
    },
  });
});

test("startInit omits untouched GitHub credentials so stored values are kept", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, async json() { return { run_id: "run-1" }; } };
  };

  await startInit({ sources: ["github"] });
  await startInit({ sources: ["github"], githubUsername: null, githubToken: null });

  assert.deepEqual(JSON.parse(calls[0].options.body), { force: false, sources: ["github"] });
  assert.deepEqual(JSON.parse(calls[1].options.body), { force: false, sources: ["github"] });
});

test("startInit omits the token when none is supplied (keep configured)", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, async json() { return { run_id: "run-1" }; } };
  };

  await startInit({ sources: ["bangumi"], bangumiUsername: "sai", bangumiToken: null });

  assert.deepEqual(JSON.parse(calls[0].options.body), {
    force: false,
    sources: ["bangumi"],
    source_options: { bangumi: { username: "sai" } },
  });
});

test("startInit force:true sends the re-init payload", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, async json() { return { run_id: "run-reinit" }; } };
  };

  await startInit({ force: true });

  assert.deepEqual(JSON.parse(calls[0].options.body), { force: true });
  assert.equal(calls[0].options.method, "POST");
});

test("startInit sends llm_concurrency when supplied", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, async json() { return { run_id: "run-1" }; } };
  };

  await startInit({ llmConcurrency: 2 });

  assert.deepEqual(JSON.parse(calls[0].options.body), { force: false, llm_concurrency: 2 });
});

test("startInit omits llm_concurrency for legacy clients", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, async json() { return { run_id: "run-1" }; } };
  };

  await startInit({});

  assert.deepEqual(JSON.parse(calls[0].options.body), { force: false });
});

test("popup settings re-init calls POST /api/init with force:true after confirm", () => {
  const source = readFileSync(resolve("popup/popup.js"), "utf8");
  const html = readFileSync(resolve("popup/popup.html"), "utf8");

  // The settings overlay owns the re-init entry (gui-init §4 entry convergence).
  assert.match(html, /id="cfgReinitBtn"/);
  assert.match(html, /id="cfgReinitStatus"/);
  // A confirm dialog guards the destructive re-pull, then force:true is sent.
  assert.match(source, /window\.confirm\(/);
  assert.match(source, /const payload = \{ force: true \};/);
  // Optional awareness/insight reset checkbox feeds reset_cognition.
  assert.match(html, /id="cfgReinitResetCognition"/);
  assert.match(html, /data-settings-ignore-dirty/);
  assert.match(source, /payload\.reset_cognition = true/);
  assert.match(source, /startInit\(payload\)/);
  // After a successful start the popup switches to the recommend tab so the
  // existing init progress panel becomes visible.
  assert.match(source, /setActiveTab\("recommend"\)/);
  assert.match(source, /renderInitProgress\(\{ running: true/);
  assert.match(source, /_startInitProgressPoll\(\)/);
});

test("popup resolves the Bangumi username omit-vs-clear before sending guided init", () => {
  const source = readFileSync(resolve("popup/popup.js"), "utf8");

  assert.match(source, /resolveInitBangumiUsername\(\{/);
  assert.match(source, /bangumiUsername:\s*bangumiUsernameOption/);
  assert.match(source, /state\.initBangumiUsernamePrefilled = true/);
});

test("popup resolves GitHub username omit-vs-clear and keeps PAT write-only", () => {
  const source = readFileSync(resolve("popup/popup.js"), "utf8");

  assert.match(source, /resolveInitGitHubUsername\(\{/);
  assert.match(source, /githubUsername:\s*githubUsernameOption/);
  assert.match(source, /githubToken:\s*githubTokenOption/);
  assert.match(source, /state\.initGitHubUsernamePrefilled = true/);
  assert.doesNotMatch(source, /state\.initGitHubTokenPrefilled/);
});

test("popup surfaces guided-init 202 warnings via the hint banner", () => {
  const source = readFileSync(resolve("popup/popup.js"), "utf8");

  assert.match(source, /startResult = await startInit\(/);
  assert.match(source, /startResult\?\.warnings/);
  assert.match(source, /setHint\(\s*\n?\s*startWarnings\.length/);
});

test("popup preserves the Bangumi username across init-panel rerenders", () => {
  const source = readFileSync(resolve("popup/popup.js"), "utf8");

  assert.match(source, /initBangumiUsername:\s*""/);
  assert.match(source, /bangumiInput\.value = state\.initBangumiUsername/);
  assert.match(
    source,
    /bangumiInput\.addEventListener\("input", \(\) => \{\s*state\.initBangumiUsername/,
  );
});

test("popup exposes cancel, offline feedback, and indeterminate init progress", () => {
  const popupJs = readFileSync(resolve("popup/popup.js"), "utf8");
  const popupHtml = readFileSync(resolve("popup/popup.html"), "utf8");
  assert.ok(popupJs.includes("handleCancelInitClick"));
  assert.ok(popupJs.includes("暂时无法连接初始化后台"));
  assert.ok(popupJs.includes('classList.toggle("indeterminate"'));
  assert.ok(popupJs.includes('progress.indeterminate ? "100%"'));
  assert.ok(popupHtml.includes('id="initCancelBtn"'));
  assert.ok(popupHtml.includes(".init-progress-bar.indeterminate"));
});

test("health helpers coalesce concurrent popup probes", async () => {
  __resetPopupHealthCacheForTests();
  const calls: Array<{ url: string; options: RequestInit }> = [];
  globalThis.fetch = (async (url: string, options: RequestInit = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { status: "ok", service: "openbiliclaw-api", embedding_ready: true };
      },
    };
  }) as unknown as typeof fetch;

  const [health, healthAgain] = await Promise.all([fetchHealth(), fetchHealth()]);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/health");
  assert.deepEqual(health, { status: "ok", service: "openbiliclaw-api", embedding_ready: true });
  assert.deepEqual(healthAgain, health);
});

test("health helpers reuse a fresh popup health result", async () => {
  __resetPopupHealthCacheForTests();
  const calls: Array<{ url: string; options: RequestInit }> = [];
  globalThis.fetch = (async (url: string, options: RequestInit = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { status: "ok", service: "openbiliclaw-api", embedding_ready: false };
      },
    };
  }) as unknown as typeof fetch;

  const health = await fetchHealth();
  const secondHealth = await fetchHealth();

  assert.equal(calls.length, 1);
  assert.equal(health?.embedding_ready, false);
  assert.deepEqual(secondHealth, health);
});

test("checkBackendStatus probes the lightweight /ping endpoint, not /health", async () => {
  __resetPopupHealthCacheForTests();
  const calls: Array<{ url: string; options: RequestInit }> = [];
  globalThis.fetch = (async (url: string, options: RequestInit = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { status: "ok", service: "openbiliclaw-api" };
      },
    };
  }) as unknown as typeof fetch;

  const online = await checkBackendStatus();

  assert.equal(online, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/ping");
});

test("checkBackendStatus falls back to /health when /ping is missing (older backend)", async () => {
  __resetPopupHealthCacheForTests();
  const calls: string[] = [];
  globalThis.fetch = (async (url: string) => {
    calls.push(url);
    if (url.endsWith("/ping")) {
      return { ok: false, status: 404, async json() { return { error: "not_found" }; } };
    }
    return {
      ok: true,
      status: 200,
      async json() {
        return { status: "ok", service: "openbiliclaw-api" };
      },
    };
  }) as unknown as typeof fetch;

  const online = await checkBackendStatus();

  assert.equal(online, true);
  assert.deepEqual(calls, [
    "http://127.0.0.1:8420/api/ping",
    "http://127.0.0.1:8420/api/health",
  ]);
});

test("checkBackendStatus reports offline when the ping request rejects", async () => {
  __resetPopupHealthCacheForTests();
  globalThis.fetch = (async () => {
    throw new TypeError("Failed to fetch");
  }) as unknown as typeof fetch;

  assert.equal(await checkBackendStatus(), false);
});

test("reshuffleRecommendations posts to reshuffle endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          items: [
            {
              id: 11,
              bvid: "BV1NEW",
              title: "新的一批",
              up_name: "UPA",
              cover_url: "//i0.hdslb.com/bfs/archive/new-cover.jpg",
              expression: "先给你捞一条新的。",
              topic_label: "",
              presented: false,
            },
          ],
        };
      },
    };
  };

  const result = await reshuffleRecommendations(["BV1CURRENT", "BV2CURRENT"]);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/recommendations/reshuffle");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.equal(
    calls[0].options.body,
    JSON.stringify({ excluded_bvids: ["BV1CURRENT", "BV2CURRENT"] }),
  );
  assert.deepEqual(result, {
    items: [
      {
        id: 11,
        bvid: "BV1NEW",
        title: "新的一批",
        up_name: "UPA",
        cover_url: "https://i0.hdslb.com/bfs/archive/new-cover.jpg",
        expression: "先给你捞一条新的。",
        topic_label: "",
        presented: false,
        item_key: "",
        content_id: "BV1NEW",
        content_url: "",
        source_platform: "bilibili",
        content_type: "video",
        body_text: "",
        published_at: "",
        published_label: "",
        view_count: 0,
        like_count: 0,
        comment_count: 0,
        share_count: 0,
        favorite_count: 0,
        danmaku_count: 0,
        rating_score: 0,
        rating_count: 0,
        source_rank: 0,
      },
    ],
  });
});

test("appendRecommendations posts excluded bvids to append endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          items: [
            {
              id: 21,
              bvid: "BV1APPEND",
              title: "追加的一条",
              up_name: "UPB",
              cover_url: "http://i0.hdslb.com/bfs/archive/append-cover.jpg",
              expression: "",
              topic_label: "",
              presented: false,
            },
          ],
        };
      },
    };
  };

  const result = await appendRecommendations(["BV1A", "BV1B"]);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/recommendations/append");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.equal(calls[0].options.body, JSON.stringify({ excluded_bvids: ["BV1A", "BV1B"] }));
  assert.deepEqual(result, {
    items: [
      {
        id: 21,
        bvid: "BV1APPEND",
        title: "追加的一条",
        up_name: "UPB",
        cover_url: "https://i0.hdslb.com/bfs/archive/append-cover.jpg",
        expression: "",
        topic_label: "",
        presented: false,
        item_key: "",
        content_id: "BV1APPEND",
        content_url: "",
        source_platform: "bilibili",
        content_type: "video",
        body_text: "",
        published_at: "",
        published_label: "",
        view_count: 0,
        like_count: 0,
        comment_count: 0,
        share_count: 0,
        favorite_count: 0,
        danmaku_count: 0,
        rating_score: 0,
        rating_count: 0,
        source_rank: 0,
      },
    ],
  });
});

test("respondToAvoidanceProbe posts to avoidance probe endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { ok: true };
      },
    };
  };

  await respondToAvoidanceProbe("浅层热点复读", "confirm", "对，这类我不想看");

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/avoidance-probes/respond");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    domain: "浅层热点复读",
    response: "confirm",
    message: "对，这类我不想看",
  });
});

test("fetchRecommendations normalizes cover urls from the recommend endpoint", async () => {
  globalThis.fetch = async () => ({
    ok: true,
    async json() {
      return {
        items: [
          {
            id: 31,
            bvid: "BV1FETCH",
            title: "初始推荐",
            up_name: "UPC",
            cover_url: "http://i1.hdslb.com/bfs/archive/fetch-cover.jpg",
            expression: "",
            topic_label: "",
            presented: 0,
          },
        ],
      };
    },
  });

  const { fetchRecommendations } = await import("../popup/popup-api.js");
  const result = await fetchRecommendations();

  assert.deepEqual(result, [
    {
      id: 31,
      bvid: "BV1FETCH",
      title: "初始推荐",
      up_name: "UPC",
      cover_url: "https://i1.hdslb.com/bfs/archive/fetch-cover.jpg",
      expression: "",
      topic_label: "",
      presented: false,
      item_key: "",
      content_id: "BV1FETCH",
      content_url: "",
      source_platform: "bilibili",
      content_type: "video",
      body_text: "",
      published_at: "",
      published_label: "",
      view_count: 0,
      like_count: 0,
      comment_count: 0,
      share_count: 0,
      favorite_count: 0,
      danmaku_count: 0,
      rating_score: 0,
      rating_count: 0,
      source_rank: 0,
    },
  ]);
});

test("fetchActivityFeed loads popup activity summaries", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          live_summary: "正在补候选",
          headline: "阿B 刚记下了你最近更吃深拆",
          items: [],
        };
      },
    };
  };

  const result = await fetchActivityFeed();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/activity-feed");
  assert.equal(calls[0].options.method, "GET");
  assert.deepEqual(result, {
    live_summary: "正在补候选",
    headline: "阿B 刚记下了你最近更吃深拆",
    items: [],
  });
});

test("backend update API helpers use backend-only update endpoints", async () => {
  const calls: { url: string; options: RequestInit }[] = [];
  globalThis.fetch = (async (url: string, options: RequestInit = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        if (url.endsWith("/api/update/apply")) {
          return { target: "backend", state: "applying", reason: "none", accepted: true };
        }
        return {
          backend: {
            state: "update_available",
            latest_tag: "backend-v0.3.92",
          },
        };
      },
    };
  }) as unknown as typeof fetch;

  await fetchUpdateStatus();
  await checkBackendUpdate();
  await applyBackendUpdate("backend-v0.3.92");

  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/update-status");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[1].url, "http://127.0.0.1:8420/api/update/check");
  assert.equal(calls[1].options.method, "POST");
  assert.equal(calls[1].options.body, JSON.stringify({ include_backend: true }));
  assert.equal(calls[2].url, "http://127.0.0.1:8420/api/update/apply");
  assert.equal(calls[2].options.method, "POST");
  assert.equal(calls[2].options.body, JSON.stringify({ target: "backend", tag: "backend-v0.3.92" }));
});

test("watch-later popup API helpers use the shared backend endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return url.includes("?")
          ? {
              items: [
                {
                  bvid: "BV1WL",
                  title: "稍后条目",
                  up_name: "测试 UP",
                  cover_url: "//i0.hdslb.com/bfs/archive/watch-later.jpg",
                  content_url: "",
                  source_platform: "",
                },
              ],
              total: 1,
            }
          : { saved: true, total: 1 };
      },
    };
  };

  const { addToWatchLater, removeFromWatchLater, watchLaterStatus } = await import(
    "../popup/popup-api.js"
  );

  await addToWatchLater("BV1WL");
  await removeFromWatchLater("BV1WL");
  await watchLaterStatus("BV1WL");
  const list = await fetchWatchLater(20, 40);

  assert.deepEqual(list, {
    items: [
      {
        bvid: "BV1WL",
        title: "稍后条目",
        up_name: "测试 UP",
        cover_url: "https://i0.hdslb.com/bfs/archive/watch-later.jpg",
        content_url: "",
        source_platform: "bilibili",
      },
    ],
    total: 1,
  });
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/watch-later");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.body, JSON.stringify({ bvid: "BV1WL" }));
  assert.equal(calls[1].url, "http://127.0.0.1:8420/api/watch-later/BV1WL");
  assert.equal(calls[1].options.method, "DELETE");
  assert.equal(calls[2].url, "http://127.0.0.1:8420/api/watch-later/BV1WL");
  assert.equal(calls[3].url, "http://127.0.0.1:8420/api/watch-later?limit=20&offset=40");
});

test("favorites popup API helpers use the shared backend endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return url.includes("?")
          ? {
              items: [
                {
                  bvid: "BV1FAV",
                  title: "收藏条目",
                  up_name: "测试 UP",
                  cover_url: "http://i0.hdslb.com/bfs/archive/favorite.jpg",
                  content_url: "",
                  source_platform: "",
                },
              ],
              total: 1,
            }
          : { saved: true, total: 1 };
      },
    };
  };

  const { addToFavorite, removeFromFavorite, favoriteStatus, fetchFavorites } = await import(
    "../popup/popup-api.js"
  );

  await addToFavorite("BV1FAV");
  await removeFromFavorite("BV1FAV");
  await favoriteStatus("BV1FAV");
  const list = await fetchFavorites(20, 40);

  assert.deepEqual(list, {
    items: [
      {
        bvid: "BV1FAV",
        title: "收藏条目",
        up_name: "测试 UP",
        cover_url: "https://i0.hdslb.com/bfs/archive/favorite.jpg",
        content_url: "",
        source_platform: "bilibili",
      },
    ],
    total: 1,
  });
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/favorites");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.body, JSON.stringify({ bvid: "BV1FAV" }));
  assert.equal(calls[1].url, "http://127.0.0.1:8420/api/favorites/BV1FAV");
  assert.equal(calls[1].options.method, "DELETE");
  assert.equal(calls[2].url, "http://127.0.0.1:8420/api/favorites/BV1FAV");
  assert.equal(calls[3].url, "http://127.0.0.1:8420/api/favorites?limit=20&offset=40");
});

test("fetchPendingDelight loads the current pending delight candidate", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          item: {
            bvid: "BV1DELIGHT",
            title: "你可能会意外喜欢的这条",
            delight_reason: "它和你最近的节奏不完全一样，但入口很对味。",
            delight_score: 0.78,
            delight_hook: "换个方向试试",
            cover_url: "//i0.hdslb.com/bfs/archive/delight-cover.jpg",
          },
        };
      },
    };
  };

  const result = await fetchPendingDelight();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/delight/pending");
  assert.equal(calls[0].options.method, "GET");
  assert.deepEqual(result, {
    bvid: "BV1DELIGHT",
    title: "你可能会意外喜欢的这条",
    delight_reason: "它和你最近的节奏不完全一样，但入口很对味。",
    delight_score: 0.78,
    delight_hook: "换个方向试试",
    cover_url: "//i0.hdslb.com/bfs/archive/delight-cover.jpg",
  });
});

test("fetchPendingDelightBatch omits limit by default so backend config applies", async () => {
  const calls: Array<{ url: string; options: RequestInit }> = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return {
      ok: true,
      async json() {
        return { items: [] };
      },
    } as Response;
  };

  await fetchPendingDelightBatch();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/delight/pending-batch");
  assert.equal(calls[0].options.method, "GET");
});

test("fetchPendingDelightBatch still forwards an explicit limit override", async () => {
  const calls: Array<{ url: string; options: RequestInit }> = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return {
      ok: true,
      async json() {
        return { items: [] };
      },
    } as Response;
  };

  await fetchPendingDelightBatch(11);

  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/delight/pending-batch?limit=11");
});

test("popup delight queue fetches do not hardcode the old fixed batch size", () => {
  const popupJs = readFileSync(resolve("popup/popup.js"), "utf8");

  assert.doesNotMatch(popupJs, /fetchPendingDelightBatch\(20\)/);
  assert.match(popupJs, /fetchPendingDelightBatch\(\)/);
});

test("fetchProfileSummary forwards limit and cursor for cognition history pagination", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          initialized: true,
          recent_cognition_updates: [],
          has_more_cognition_updates: false,
          next_cognition_cursor: "",
        };
      },
    };
  };

  await fetchProfileSummary({ limit: 3, cursor: "6" });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/profile-summary?limit=3&cursor=6");
  assert.equal(calls[0].options.method, "GET");
});

test("fetchConfig requests the masked config snapshot", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          language: "zh",
          llm: {
            default_provider: "gemini",
            gemini: { api_key: "test-key", model: "gemini-2.5-flash" },
            embedding: {
              provider: "gemini",
              model: "gemini-embedding-001",
              similarity_threshold: 0.85,
            },
          },
        };
      },
    };
  };

  const result = await fetchConfig();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/config");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(result.llm.default_provider, "gemini");
  assert.equal(result.llm.gemini.api_key, "test-key");
  assert.equal(result.llm.embedding.provider, "gemini");
  assert.equal(result.llm.embedding.model, "gemini-embedding-001");
  assert.equal(result.llm.embedding.similarity_threshold, 0.85);
});

test("fetchConfig caches successful config snapshots in chrome storage", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const writes: Array<Record<string, unknown>> = [];
  const storage: Record<string, unknown> = {};
  (globalThis as { chrome?: unknown }).chrome = {
    storage: {
      local: {
        get(key: string, callback: (items: Record<string, unknown>) => void) {
          callback({ [key]: storage[key] });
        },
        set(items: Record<string, unknown>, callback: () => void) {
          writes.push(items);
          Object.assign(storage, items);
          callback();
        },
      },
    },
  };
  globalThis.fetch = async () => ({
    ok: true,
    async json() {
      return {
        language: "zh",
        llm: {
          default_provider: "openai",
          openai: { api_key: "sk-test" },
        },
      };
    },
  }) as Response;

  try {
    const result = await fetchConfig();
    const cached = await readCachedConfigSnapshot();

    assert.equal(result.llm.default_provider, "openai");
    assert.equal(writes.length, 1);
    assert.ok(writes[0]["openbiliclaw.config_cache"]);
    assert.equal(cached?.config.llm.openai.api_key, "sk-test");
    assert.match(cached?.cached_at ?? "", /^\d{4}-\d{2}-\d{2}T/);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
  }
});

test("cacheConfigSnapshot no-ops when chrome storage is unavailable", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  delete (globalThis as { chrome?: unknown }).chrome;

  try {
    const snapshot = await cacheConfigSnapshot({ language: "zh" });
    assert.equal(snapshot, null);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
  }
});

test("fetchSourceShareSuggestion loads source-share recommendation", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          event_counts: { bilibili: 9, youtube: 4 },
          enabled_sources: { bilibili: true, youtube: true },
          suggested_shares: { bilibili: 8, youtube: 5 },
        };
      },
    };
  };

  const result = await fetchSourceShareSuggestion();

  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:8420/api/config/source-share-suggestion",
  );
  assert.equal(calls[0].options.method, "GET");
  assert.equal(result.suggested_shares.youtube, 5);
});

test("fetchSourceShareSuggestion posts current settings overrides when provided", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          event_counts: { bilibili: 9, youtube: 4 },
          enabled_sources: { bilibili: true, youtube: true },
          suggested_shares: { bilibili: 6, youtube: 4 },
        };
      },
    };
  };

  const result = await fetchSourceShareSuggestion({
    enabled_sources: {
      bilibili: true,
      xiaohongshu: false,
      douyin: false,
      youtube: true,
    },
    configured_shares: {
      bilibili: 6,
      xiaohongshu: 2,
      douyin: 1,
      youtube: 2,
    },
  });

  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:8420/api/config/source-share-suggestion",
  );
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    enabled_sources: {
      bilibili: true,
      xiaohongshu: false,
      douyin: false,
      youtube: true,
    },
    configured_shares: {
      bilibili: 6,
      xiaohongshu: 2,
      douyin: 1,
      youtube: 2,
    },
  });
  assert.equal(result.suggested_shares.youtube, 4);
});

test("probeConfigService posts no-write config probe payload", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          ok: true,
          kind: "llm",
          provider: "openai",
          message: "LLM provider is available.",
        };
      },
    };
  };

  const result = await probeConfigService("llm", {
    llm: { default_provider: "openai", openai: { api_key: "sk-test" } },
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/config/probe-service");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    kind: "llm",
    config: {
      llm: { default_provider: "openai", openai: { api_key: "sk-test" } },
    },
  });
  assert.equal(result.ok, true);
  assert.equal(result.provider, "openai");
});

test("probeConfigService targets one routed LLM instance when requested", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { ok: true, kind: "llm_instance", instance_id: "relay-backup" };
      },
    };
  };

  await probeConfigService(
    "llm_instance",
    {
      llm: {
        routing_version: 2,
        instances: { "relay-backup": { provider_type: "openai_compatible" } },
        default_chain: ["relay-backup"],
      },
    },
    "relay-backup",
  );

  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    kind: "llm_instance",
    config: {
      llm: {
        routing_version: 2,
        instances: { "relay-backup": { provider_type: "openai_compatible" } },
        default_chain: ["relay-backup"],
      },
    },
    instance_id: "relay-backup",
  });
});

test("updateConfig sends PUT with embedding config", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          ok: true,
          config: { language: "zh", llm: { embedding: { provider: "openai", model: "text-embedding-3-small", similarity_threshold: 0.78 } } },
          message: "配置已保存。",
          reloaded: true,
        };
      },
    };
  };

  const payload = {
    llm: {
      default_provider: "openai",
      openai: { api_key: "sk-test", model: "gpt-4o" },
      embedding: {
        provider: "openai",
        model: "text-embedding-3-small",
        similarity_threshold: 0.78,
      },
    },
  };

  const result = await updateConfig(payload);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/config");
  assert.equal(calls[0].options.method, "PUT");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");

  const sentBody = JSON.parse(calls[0].options.body);
  assert.equal(sentBody.llm.embedding.provider, "openai");
  assert.equal(sentBody.llm.embedding.model, "text-embedding-3-small");
  assert.equal(sentBody.llm.embedding.similarity_threshold, 0.78);
  assert.equal(sentBody.llm.openai.api_key, "sk-test");

  assert.equal(result.ok, true);
  assert.equal(result.reloaded, true);
});

test("updateConfig accepts persisted 202 responses", async () => {
  globalThis.fetch = (async () => ({
    ok: true,
    status: 202,
    async json() {
      return { ok: true, apply_state: "queued", apply_revision: 9 };
    },
  })) as unknown as typeof fetch;

  const result = await updateConfig({ language: "zh" });

  assert.equal(result.apply_state, "queued");
  assert.equal(result.apply_revision, 9);
});

test("updateConfig preserves structured details from validation errors", async () => {
  const details = {
    ok: false,
    reloaded: false,
    rollback_applied: false,
    config: {
      issues: [
        {
          field: "llm",
          message: "LLM registry would fail to build",
          severity: "blocking",
        },
      ],
    },
    message: "配置校验失败，未写入 config.toml。",
  };
  globalThis.fetch = async () => ({
    ok: false,
    status: 400,
    async json() {
      return details;
    },
  });

  await assert.rejects(
    () => updateConfig({ reset_fields: ["llm.openai.api_key"] }),
    (error: any) => {
      assert.equal(error.message, "/config request failed: 400");
      assert.equal(error.status, 400);
      assert.deepEqual(error.details, details);
      return true;
    },
  );
});

test("startChatTurn posts durable chat turn metadata", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          turn_id: "turn-abc",
          session: "popup",
          scope: "delight",
          subject_id: "BV1DL",
          subject_title: "复杂系统入门",
          message: "我想聊聊这条",
          reply: "",
          status: "pending",
          error: "",
          created_at: "2026-05-15 10:00:00",
          updated_at: "2026-05-15 10:00:00",
        };
      },
    };
  };

  const result = await startChatTurn({
    turnId: "turn-abc",
    session: "popup",
    scope: "delight",
    subjectId: "BV1DL",
    subjectTitle: "复杂系统入门",
    message: "我想聊聊这条",
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/chat/turns");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    turn_id: "turn-abc",
    session: "popup",
    scope: "delight",
    subject_id: "BV1DL",
    subject_title: "复杂系统入门",
    message: "我想聊聊这条",
    streaming: false,
  });
  assert.equal(result.status, "pending");
});

test("fetchChatTurn and fetchChatTurns read durable chat state", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        if (String(url).endsWith("/api/chat/turns/turn-abc")) {
          return {
            turn_id: "turn-abc",
            session: "popup",
            scope: "chat",
            message: "你好",
            reply: "你好，我在。",
            status: "completed",
          };
        }
        return {
          items: [
            {
              turn_id: "turn-abc",
              session: "popup",
              scope: "chat",
              message: "你好",
              reply: "你好，我在。",
              status: "completed",
            },
          ],
        };
      },
    };
  };

  const turn = await fetchChatTurn("turn-abc");
  const history = await fetchChatTurns({ session: "popup", scope: "chat", limit: 10 });

  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/chat/turns/turn-abc");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(
    calls[1].url,
    "http://127.0.0.1:8420/api/chat/turns?session=popup&scope=chat&limit=10",
  );
  assert.equal(calls[1].options.method, "GET");
  assert.equal(turn.reply, "你好，我在。");
  assert.equal(history.items[0].turn_id, "turn-abc");
});

test("popup-api requests honor configured backend host and port from chrome.storage.local", async () => {
  // Reset module cache so the previous tests' default-port resolution
  // doesn't shadow the stubbed chrome.storage value.
  __resetBackendEndpointForTests();
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  (globalThis as { chrome?: unknown }).chrome = {
    storage: {
      local: {
        get(_key: string, callback: (items: Record<string, unknown>) => void) {
          callback({
            popup_backend_endpoint: {
              host: "192.168.1.100",
              port: 19090,
              basePath: "/api",
            },
          });
        },
      },
    },
  };

  const calls: Array<{ url: string; options: { method?: string } }> = [];
  globalThis.fetch = (async (url: string, options: { method?: string }) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { language: "zh" };
      },
    };
  }) as unknown as typeof fetch;

  try {
    await fetchConfig();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "http://192.168.1.100:19090/api/config");
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    __resetBackendEndpointForTests();
  }
});

test("requestJson aborts fetch after timeoutMs", async () => {
  globalThis.fetch = (async (_url: string, options: { signal?: AbortSignal }) => {
    return new Promise((_resolve, reject) => {
      if (!options.signal) {
        setTimeout(() => reject(new Error("missing abort signal")), 100);
        return;
      }
      options.signal.addEventListener("abort", () => {
        reject(options.signal?.reason ?? new DOMException("Aborted", "AbortError"));
      });
    });
  }) as unknown as typeof fetch;

  await assert.rejects(
    requestJson("/slow", { method: "GET", timeoutMs: 20 }),
    (error: unknown) => error instanceof Error && error.name === "AbortError",
  );
});

test("requestJson without timeout preserves no-signal fetch behavior", async () => {
  const calls: Array<{ signal?: AbortSignal }> = [];
  globalThis.fetch = (async (_url: string, options: { signal?: AbortSignal }) => {
    calls.push(options);
    return {
      ok: true,
      async json() {
        return { ok: true };
      },
    };
  }) as unknown as typeof fetch;

  const result = await requestJson("/fast", { method: "GET" });

  assert.deepEqual(result, { ok: true });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].signal, undefined);
});

test("requestJson preserves caller abort reason before timeout fires", async () => {
  const controller = new AbortController();
  const reason = new DOMException("caller cancelled", "AbortError");
  globalThis.fetch = (async (_url: string, options: { signal?: AbortSignal }) => {
    return new Promise((_resolve, reject) => {
      options.signal?.addEventListener("abort", () => {
        reject(options.signal?.reason ?? new DOMException("Aborted", "AbortError"));
      });
      queueMicrotask(() => controller.abort(reason));
    });
  }) as unknown as typeof fetch;

  await assert.rejects(
    requestJson("/caller-abort", {
      method: "GET",
      signal: controller.signal,
      timeoutMs: 200,
    }),
    (error: unknown) => error === reason,
  );
});

test("updateConfig uses the shared 60s config PUT timeout", async () => {
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  const delays: number[] = [];
  globalThis.setTimeout = ((callback: TimerHandler, delay?: number) => {
    delays.push(Number(delay));
    queueMicrotask(() => {
      if (typeof callback === "function") callback();
    });
    return 1 as unknown as ReturnType<typeof setTimeout>;
  }) as typeof setTimeout;
  globalThis.clearTimeout = ((_id?: unknown) => undefined) as typeof clearTimeout;
  globalThis.fetch = (async (_url: string, options: { signal?: AbortSignal }) => {
    return new Promise((_resolve, reject) => {
      options.signal?.addEventListener("abort", () => {
        reject(options.signal?.reason ?? new DOMException("Aborted", "AbortError"));
      });
    });
  }) as unknown as typeof fetch;

  try {
    await assert.rejects(
      updateConfig({ language: "zh" }),
      (error: unknown) => error instanceof Error && error.name === "AbortError",
    );
    assert.deepEqual(delays, [60_000]);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});

test("streamChatTurn read watchdog aborts a silently stalled SSE stream", async () => {
  // The shared agent-chat helper (copied to popup/shared at build time)
  // provides createSseReadWatchdog + the SSE parser.
  await import("../../src/openbiliclaw/web/shared/agent-chat.js");
  __resetBackendEndpointForTests();
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  (globalThis as { chrome?: unknown }).chrome = {
    storage: {
      local: {
        get(_key: string, callback: (items: Record<string, unknown>) => void) {
          callback({});
        },
      },
    },
  };

  let capturedSignal: AbortSignal | null = null;
  globalThis.fetch = (async (_url: string, options: { signal?: AbortSignal }) => {
    capturedSignal = options?.signal ?? null;
    return {
      ok: true,
      body: {
        getReader: () => ({
          read: () =>
            new Promise((_resolve, reject) => {
              // 永不返回字节，只在请求被 abort 时 reject —— 模拟僵尸流。
              capturedSignal?.addEventListener("abort", () =>
                reject((capturedSignal as AbortSignal).reason),
              );
            }),
        }),
      },
    };
  }) as unknown as typeof fetch;

  try {
    const startedAt = Date.now();
    await assert.rejects(streamChatTurn({ turnId: "t-stall", message: "你好", watchdogMs: 30 }));
    assert.ok(capturedSignal, "fetch should receive the watchdog abort signal");
    assert.ok(Date.now() - startedAt < 5_000, "watchdog abort should settle the stream quickly");
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    __resetBackendEndpointForTests();
  }
});

test("streamChatTurn completes when heartbeat comments keep the stream alive", async () => {
  await import("../../src/openbiliclaw/web/shared/agent-chat.js");
  __resetBackendEndpointForTests();
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  (globalThis as { chrome?: unknown }).chrome = {
    storage: {
      local: {
        get(_key: string, callback: (items: Record<string, unknown>) => void) {
          callback({});
        },
      },
    },
  };

  const chunks = [": ping\n\n", 'event: content\ndata: {"delta":"你"}\n\n', 'event: done\ndata: {"reply":"你好"}\n\n'];
  globalThis.fetch = (async () => ({
    ok: true,
    body: {
      getReader: () => {
        let index = 0;
        return {
          read: async () => {
            if (index >= chunks.length) return { done: true };
            await new Promise((resolve) => setTimeout(resolve, 15));
            const value = new TextEncoder().encode(chunks[index]);
            index += 1;
            return { done: false, value };
          },
        };
      },
    },
  })) as unknown as typeof fetch;

  try {
    const deltas: string[] = [];
    let doneReply = "";
    await streamChatTurn({
      turnId: "t-ok",
      message: "你好",
      watchdogMs: 200,
      onContent: (delta: string) => deltas.push(delta),
      onDone: (data: { reply?: string }) => {
        doneReply = String(data?.reply || "");
      },
    });
    assert.deepEqual(deltas, ["你"]);
    assert.equal(doneReply, "你好");
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    __resetBackendEndpointForTests();
  }
});
