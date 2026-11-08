/**
 * Tests for the Douyin content-script helpers and account API orchestration.
 *
 * Module isolation: zero imports from extension/src/content/xhs/.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  classifyDouyinDiscoveryCompletion,
  classifyDouyinScopeCompletion,
  createScrollRoundController,
  DouyinPassiveDiscoveryReplayBuffer,
  douyinDiscoveryExecutionPolicy,
  filterDiscoveryItemsForScope,
  isDouyinSearchResultUrl,
  isSameWindowSameOriginDouyinMessage,
  isValidFeedExecuteMessage,
  isValidHotExecuteMessage,
  isValidSearchExecuteMessage,
  isValidScopeExecuteMessage,
  resolveHotSeedAwemeId,
  runHot,
  runScope,
  shouldReplayEarlyDiscoveryItems,
  waitForCurrentVideoAwemeId,
} from "../src/content/douyin.ts";
import type { DouyinBootstrapItem, DouyinScope } from "../src/main/dy-fetch-tap.ts";
import { installApiHarvester } from "../src/main/dy-fetch-tap.ts";

function scopeMessage(scope: DouyinScope) {
  return { task_id: "account-task", scope, max_items_per_scope: 1,
    max_scroll_rounds: 5, max_stagnant_scroll_rounds: 2 };
}

function scopeItem(scope: DouyinScope): DouyinBootstrapItem {
  return { scope, aweme_id: "owned-video", creator_sec_uid: "followed-creator",
    title: "Account record", author: "Creator", author_sec_uid: "creator", cover_url: "",
    url: "https://www.douyin.com/video/owned-video" };
}

for (const scope of ["dy_post", "dy_like", "dy_collect", "dy_follow"] as const) {
  for (const kind of ["populated", "empty", "partial"] as const) {
    test(`${scope} ${kind} API result never imports page recommendation cards`, async t => {
      const previous = Object.getOwnPropertyDescriptor(globalThis, "document");
      let domReads = 0;
      Object.defineProperty(globalThis, "document", { configurable: true, value: {
        querySelectorAll: () => {
          domReads += 1;
          throw new Error("Unrelated recommendation cards must not be harvested");
        },
      } });
      t.after(() => {
        if (previous) Object.defineProperty(globalThis, "document", previous);
        else Reflect.deleteProperty(globalThis, "document");
      });
      const items = kind === "empty" ? [] : [scopeItem(scope)];
      let apiCalls = 0;
      const result = await runScope(scopeMessage(scope), {
        navigate: async requestedScope => {
          assert.equal(requestedScope, scope);
          return { page_url: "https://www.douyin.com/user/self", profile_link_found: true, sub_tab_found: true };
        },
        prepare: async () => {},
        identity: async () => ({ secUid: "MS4wSelf", source: "profile_self" }),
        api: async (requestedScope, secUid, maxItems) => {
          apiCalls += 1;
          assert.deepEqual([requestedScope, secUid, maxItems], [scope, "MS4wSelf", 1]);
          return { items, pages: 1, ...(kind === "partial" ? { error: "HTTP 429" } : {}) };
        },
      });
      assert.equal(apiCalls, 1);
      assert.equal(domReads, 0);
      assert.deepEqual(result.items, items);
      assert.equal(result.scope_count, items.length);
      assert.equal(result.status, kind === "partial" ? "degraded" : kind === "empty" ? "empty" : "ok");
      assert.equal(result.error, kind === "partial" ? "HTTP 429" : undefined);
      assert.equal(result.debug?.api_items_harvested, items.length);
      assert.equal(result.debug?.dom_items_harvested, 0);
      assert.equal(result.debug?.sec_uid_source, "profile_self");
    });
  }
}

test("unavailable self identity degrades without reading account APIs or DOM", async () => {
  const result = await runScope(scopeMessage("dy_collect"), {
    navigate: async () => ({ page_url: "https://www.douyin.com/user/self", profile_link_found: true, sub_tab_found: true }),
    prepare: async () => {},
    identity: async () => ({ secUid: "", source: "", error: "identity_unavailable" }),
    api: async () => { throw new Error("Account API requires self identity"); },
  });
  assert.deepEqual(result.items, []);
  assert.equal(result.scope_count, 0);
  assert.equal(result.status, "degraded");
  assert.equal(result.error, "identity_unavailable");
});

test("a failed first API page stays degraded and empty", async () => {
  const result = await runScope(scopeMessage("dy_like"), {
    navigate: async () => ({ page_url: "https://www.douyin.com/user/self", profile_link_found: true, sub_tab_found: true }),
    prepare: async () => {},
    identity: async () => ({ secUid: "MS4wSelf", source: "profile_self" }),
    api: async () => ({ items: [], pages: 0, error: "HTTP 404" }),
  });
  assert.equal(result.status, "degraded");
  assert.equal(result.error, "HTTP 404");
  assert.deepEqual(result.items, []);
  assert.equal(result.scope_count, 0);
});

test("an unobserved API response cannot be reported as affirmative empty", async () => {
  const result = await runScope(scopeMessage("dy_post"), {
    navigate: async () => ({ page_url: "https://www.douyin.com/user/self", profile_link_found: true, sub_tab_found: true }),
    prepare: async () => {},
    identity: async () => ({ secUid: "MS4wSelf", source: "profile_self" }),
    api: async () => ({ items: [], pages: 0 }),
  });
  assert.equal(result.status, "degraded");
  assert.equal(result.error, "api_response_unobserved");
  assert.deepEqual(result.items, []);
});

test("an API exception degrades without importing page observations", async () => {
  const result = await runScope(scopeMessage("dy_like"), {
    navigate: async () => ({ page_url: "https://www.douyin.com/user/self", profile_link_found: true, sub_tab_found: true }),
    prepare: async () => {},
    identity: async () => ({ secUid: "MS4wSelf", source: "profile_self" }),
    api: async () => { throw new Error("Network unavailable"); },
  });
  assert.equal(result.status, "degraded");
  assert.equal(result.error, "Network unavailable");
  assert.deepEqual(result.items, []);
});

test("the default scope executor keeps a real bridge empty response authoritative", async t => {
  const originals = ["window", "document", "location"].map(key => [
    key, Object.getOwnPropertyDescriptor(globalThis, key),
  ] as const);
  t.after(() => {
    for (const [key, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
  });
  const fetchPaths: string[] = [];
  class FakeWindow extends EventTarget {
    location = new URL("https://www.douyin.com/user/self");
    fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), this.location.origin);
      fetchPaths.push(url.pathname);
      assert.equal(init?.credentials, "include");
      if (url.pathname === "/aweme/v1/web/user/profile/self/") {
        assert.equal(url.searchParams.get("device_platform"), "webapp");
        assert.equal(url.searchParams.get("aid"), "6383");
        assert.equal(url.searchParams.get("channel"), "channel_pc_web");
        assert.equal(url.searchParams.get("cookie_enabled"), "true");
        return Response.json({ status_code: 0, user: { sec_uid: "MS4wSelf" } });
      }
      assert.equal(url.pathname, "/aweme/v1/web/aweme/post/");
      return Response.json({ status_code: 0, has_more: false, aweme_list: [] });
    };
    postMessage(data: { type: string; requestId: string }): void {
      const respond = (payload: unknown) => {
        const event = new MessageEvent("message", { data: payload, origin: this.location.origin });
        Object.defineProperty(event, "source", { value: this });
        this.dispatchEvent(event);
      };
      queueMicrotask(() => {
        if (data.type === "OPENBILICLAW_DOUYIN_API_REQUEST") {
          // Recommendation traffic may arrive while the API request is pending.
          respond({ type: "OPENBILICLAW_DOUYIN_AWEME_PAGE", scope: "dy_post",
            items: [{ ...scopeItem("dy_post"), aweme_id: "unrelated-recommendation" }] });
        }
        respond(data);
      });
    }
  }
  const fakeWindow = new FakeWindow();
  installApiHarvester(fakeWindow as unknown as Window);
  const document = {
    querySelector: () => null,
    querySelectorAll: (selector: string) => {
      assert.equal(selector.includes('/video/'), false, "must not scrape video cards");
      return [];
    },
    getElementById: () => null,
  };
  for (const [key, value] of Object.entries({ window: fakeWindow, location: fakeWindow.location, document })) {
    Object.defineProperty(globalThis, key, { configurable: true, value });
  }
  const result = await runScope(scopeMessage("dy_post"));
  assert.equal(result.status, "empty");
  assert.deepEqual(result.items, []);
  assert.equal(result.scope_count, 0);
  assert.equal(result.debug?.sec_uid_source, "profile_self");
  assert.deepEqual(fetchPaths, ["/aweme/v1/web/user/profile/self/", "/aweme/v1/web/aweme/post/"]);
});

test("hot navigation resumes collection without resubmitting the board word", async () => {
  await runHot({
    task_id: "hot-navigation", sentence_id: "sentence", word: "热点", max_items: 12,
    resume_after_navigation: true,
  }, {
    search: async input => {
      assert.equal(input.resume_after_navigation, true);
      return { task_id: input.task_id, keyword: input.keyword, items: [], scope_count: 0, status: "empty" };
    },
    related: async () => { throw new Error("word search must not use related"); },
  });
});

test("the observed verification page stops hot collection before any upstream collector", async t => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "document");
  Object.defineProperty(globalThis, "document", { configurable: true, value: { title: "验证码中间页" } });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "document", previous);
    else Reflect.deleteProperty(globalThis, "document");
  });
  let upstreamCalls = 0;
  const result = await runHot({ task_id: "challenge", sentence_id: "one", word: "热点", max_items: 12 }, {
    search: async input => {
      upstreamCalls += 1;
      return { task_id: input.task_id, keyword: input.keyword, items: [], scope_count: 0, status: "empty" };
    },
    related: async () => { throw new Error("must stop before related too"); },
  });
  assert.equal(upstreamCalls, 0);
  assert.equal(result.status, "failed");
  assert.equal(result.error, "captcha_required");
});

test("positive partial videos cannot hide an explicit rate-limit response", () => {
  assert.deepEqual(classifyDouyinDiscoveryCompletion({
    source: "search", itemCount: 2, apiError: "HTTP 429",
    uiTriggered: true, searchNavigationOk: true,
  }), { status: "failed", error: "api_rate_limited" });
});

test("hot execution rejects a malformed navigation resume flag", () => {
  assert.equal(isValidHotExecuteMessage({
    task_id: "hot-navigation", sentence_id: "sentence", word: "热点", max_items: 12,
    resume_after_navigation: "true",
  }), false);
});

for (const status of ["ok", "failed"] as const) {
  test(`hot-board words use the native search collector and preserve ${status}`, async () => {
    const calls: string[] = [];
    const msg = { task_id: "hot-task", sentence_id: "hot-sentence", word: "真实热点词", seed_aweme_id: "board-seed", max_items: 12 };
    const result = await runHot(msg, {
      search: async (input) => {
        calls.push("search");
        assert.equal(input.keyword, msg.word);
        assert.equal(input.task_id, msg.task_id);
        assert.equal(input.max_items, 12);
        return {
          task_id: input.task_id, keyword: input.keyword, status,
          items: status === "ok" ? [{ scope: "dy_search", aweme_id: "news-video", title: "热点对应新闻" }] : [],
          scope_count: status === "ok" ? 1 : 0,
          ...(status === "failed" ? { error: "api_rate_limited" } : {}),
        };
      },
      related: async (input) => {
        calls.push("related");
        return { task_id: input.task_id, sentence_id: input.sentence_id, word: input.word, status: "ok", items: [{ scope: "dy_hot", aweme_id: "unrelated-game", title: "其他游戏" }], scope_count: 1 };
      },
    });
    assert.deepEqual(calls, ["search"]);
    assert.equal(result.status, status);
    assert.equal(result.scope_count, status === "ok" ? 1 : 0);
    if (status === "ok") {
      assert.deepEqual(result.items.map(i => [i.aweme_id, i.scope, i.sentence_id, i.hot_word]), [["news-video", "dy_hot", msg.sentence_id, msg.word]]);
    } else {
      assert.equal(result.error, "api_rate_limited");
      assert.deepEqual(result.items, []);
    }
  });
}

test("hot without a word retains the seed-related collector", async () => {
  const result = await runHot({ task_id: "hot-task", sentence_id: "hot-sentence", word: "", seed_aweme_id: "seed", max_items: 3 }, {
    search: async () => { throw new Error("wordless task must not search"); },
    related: async msg => ({ task_id: msg.task_id, sentence_id: msg.sentence_id, word: msg.word, items: [], scope_count: 0, status: "empty" }),
  });
  assert.equal(result.status, "empty");
});

test("hot without UI navigation uses its board seed without waiting for a video route", async (t) => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "location");
  Object.defineProperty(globalThis, "location", {
    configurable: true,
    value: { href: "https://www.douyin.com/video/9999999999999999999" },
  });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "location", previous);
    else Reflect.deleteProperty(globalThis, "location");
  });
  assert.equal(await resolveHotSeedAwemeId(false, "7553058116880930083"), "7553058116880930083");
  assert.equal(await resolveHotSeedAwemeId(false, ""), "");
  assert.equal(await resolveHotSeedAwemeId(true, "7553058116880930083"), "9999999999999999999");
});

test("video route wait honors elapsed time when background timers are delayed", async (t) => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "location");
  Object.defineProperty(globalThis, "location", {
    configurable: true, value: { href: "https://www.douyin.com/jingxuan" },
  });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "location", previous);
    else Reflect.deleteProperty(globalThis, "location");
  });
  let elapsed = 0;
  t.mock.method(performance, "now", () => elapsed);
  t.mock.method(globalThis, "setTimeout", (callback: () => void) => {
    elapsed += 1_000;
    queueMicrotask(callback);
    return 0;
  });
  assert.equal(await waitForCurrentVideoAwemeId(2_000), "");
  assert.ok(elapsed <= 3_000, `2s route wait used ${elapsed}ms with delayed timers`);
});

test("passive discovery buffer replays early items with dedupe, TTL, and a hard cap", () => {
  let now = 1_000;
  const buffer = new DouyinPassiveDiscoveryReplayBuffer(2, 100, () => now);
  const first = { scope: "dy_feed", aweme_id: "feed-1", title: "first" };
  const second = { scope: "dy_feed", aweme_id: "feed-2", title: "second" };
  const third = { scope: "dy_feed", aweme_id: "feed-3", title: "third" };

  assert.equal(buffer.ingest("dy_feed", [first, second, null, { scope: "dy_feed" }]), 2);
  assert.equal(buffer.ingest("dy_search", []), 0);
  assert.equal(buffer.ingest("unknown", [third]), 0);
  assert.equal(buffer.ingest("dy_feed", [{ ...first, title: "updated" }, third]), 1);
  const drained = buffer.drain("dy_feed");
  assert.equal(drained.responsesObserved, 2);
  assert.deepEqual(
    drained.items.map((item) => [item.aweme_id, item.title]),
    [
      ["feed-1", "updated"],
      ["feed-3", "third"],
    ],
  );
  assert.deepEqual(buffer.drain("dy_feed"), { items: [], responsesObserved: 0 });
  assert.deepEqual(buffer.drain("dy_search"), { items: [], responsesObserved: 1 });

  buffer.ingest("dy_feed", [first]);
  now += 101;
  assert.deepEqual(buffer.drain("dy_feed"), { items: [], responsesObserved: 0 });
});

test("discovery completion keeps harvested items despite local capture failures", () => {
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "search",
      itemCount: 1,
      injectStatus: "error: injection failed after DOM rendered",
      fetchTapInstallStatus: "unknown",
      apiError: "timeout",
      uiTriggered: false,
      searchNavigationOk: false,
    }),
    { status: "ok" },
  );
});

test("discovery completion reports explicit fetch-tap injection failures", () => {
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "search",
      itemCount: 0,
      injectStatus: "error: Could not load main/dy-fetch-tap.js",
      fetchTapInstallStatus: "installed",
      uiTriggered: true,
      searchNavigationOk: true,
    }),
    { status: "failed", error: "fetch_tap_injection_failed" },
  );
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "feed",
      itemCount: 0,
      injectStatus: "scripting_api_missing",
      fetchTapInstallStatus: "installed",
      alternateCollectionCompleted: true,
    }),
    { status: "failed", error: "fetch_tap_injection_failed" },
  );
});

test("discovery completion rejects unconfirmed fetch-tap state", () => {
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "search",
      itemCount: 0,
      fetchTapInstallStatus: "unknown",
      uiTriggered: true,
      searchNavigationOk: true,
    }),
    { status: "failed", error: "fetch_tap_status_unknown" },
  );
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "hot",
      itemCount: 0,
      fetchTapInstallStatus: "skipped_no_sdk",
      uiTriggered: true,
    }),
    { status: "failed", error: "fetch_tap_sdk_unavailable" },
  );
});

test("discovery completion maps API failures to stable non-sensitive codes", () => {
  const classifyApiError = (apiError: string) =>
    classifyDouyinDiscoveryCompletion({
      source: "feed",
      itemCount: 0,
      fetchTapInstallStatus: "installed",
      apiError,
    });

  assert.deepEqual(classifyApiError("Error: timeout"), {
    status: "failed",
    error: "api_timeout",
  });
  assert.deepEqual(classifyApiError("HTTP 503: upstream details"), {
    status: "failed",
    error: "api_http_error",
  });
  assert.deepEqual(classifyApiError("HTTP 429: too many requests"), {
    status: "failed",
    error: "api_rate_limited",
  });
  assert.deepEqual(classifyApiError("credential-shaped internal failure: secret=redacted"), {
    status: "failed",
    error: "api_collection_failed",
  });
});

test("discovery completion reports failed search and hot UI paths", () => {
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "search",
      itemCount: 0,
      fetchTapInstallStatus: "installed",
      uiTriggered: false,
      searchNavigationOk: false,
    }),
    { status: "failed", error: "search_ui_not_triggered" },
  );
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "search",
      itemCount: 0,
      fetchTapInstallStatus: "installed",
      uiTriggered: true,
      searchNavigationOk: false,
    }),
    { status: "failed", error: "search_navigation_failed" },
  );
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "hot",
      itemCount: 0,
      fetchTapInstallStatus: "installed",
      uiTriggered: false,
    }),
    { status: "failed", error: "hot_ui_not_triggered" },
  );
});

test("discovery completion only accepts clean empty collection paths", () => {
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "search",
      itemCount: 0,
      injectStatus: "ok_file=main/dy-fetch-tap.js",
      fetchTapInstallStatus: "installed",
      uiTriggered: true,
      searchNavigationOk: true,
    }),
    { status: "empty" },
  );
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "hot",
      itemCount: 0,
      fetchTapInstallStatus: "installed",
      uiTriggered: true,
    }),
    { status: "empty" },
  );
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "feed",
      itemCount: 0,
      fetchTapInstallStatus: "installed",
      passiveResponsesObserved: 0,
      domItemsHarvested: 0,
    }),
    { status: "failed", error: "feed_no_observed_response" },
  );
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "feed",
      itemCount: 0,
      fetchTapInstallStatus: "installed",
      passiveResponsesObserved: 1,
      domItemsHarvested: 0,
    }),
    { status: "empty" },
  );
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "feed",
      itemCount: 0,
      fetchTapInstallStatus: "unknown",
    }),
    { status: "failed", error: "fetch_tap_status_unknown" },
  );
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "feed",
      itemCount: 0,
      fetchTapInstallStatus: "unknown",
      alternateCollectionCompleted: true,
    }),
    { status: "empty" },
  );
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "feed",
      itemCount: 0,
      fetchTapInstallStatus: "skipped_no_sdk",
      alternateCollectionCompleted: true,
    }),
    { status: "failed", error: "fetch_tap_sdk_unavailable" },
  );
  assert.deepEqual(
    classifyDouyinDiscoveryCompletion({
      source: "search",
      itemCount: 0,
      fetchTapInstallStatus: "unknown",
      uiTriggered: true,
      searchNavigationOk: true,
      alternateCollectionCompleted: true,
    }),
    { status: "empty" },
  );
});

test("Douyin bridge messages require the same Window and page origin", () => {
  const target = {
    location: { origin: "https://www.douyin.com" },
  } as unknown as Window;
  assert.equal(
    isSameWindowSameOriginDouyinMessage(
      {
        source: target,
        origin: "https://www.douyin.com",
      } as MessageEvent,
      target,
    ),
    true,
  );
  assert.equal(
    isSameWindowSameOriginDouyinMessage(
      {
        source: {} as Window,
        origin: "https://www.douyin.com",
      } as MessageEvent,
      target,
    ),
    false,
  );
  assert.equal(
    isSameWindowSameOriginDouyinMessage(
      {
        source: target,
        origin: "https://attacker.example",
      } as MessageEvent,
      target,
    ),
    false,
  );
});

test("classifyDouyinScopeCompletion marks missing identity and API errors degraded", () => {
  assert.deepEqual(
    classifyDouyinScopeCompletion({
      itemCount: 8,
      secUid: "",
      apiError: "",
      identityError: "identity_unavailable",
    }),
    { status: "degraded", error: "identity_unavailable" },
  );
  assert.deepEqual(
    classifyDouyinScopeCompletion({
      itemCount: 18,
      secUid: "MS4wUser",
      apiError: "HTTP 429",
    }),
    { status: "degraded", error: "HTTP 429" },
  );
  assert.deepEqual(
    classifyDouyinScopeCompletion({
      itemCount: 18,
      secUid: "MS4wUser",
      apiError: "",
    }),
    { status: "ok" },
  );
  assert.deepEqual(
    classifyDouyinScopeCompletion({
      itemCount: 0,
      secUid: "MS4wUser",
      apiError: "",
    }),
    { status: "empty" },
  );
});

test("isValidScopeExecuteMessage accepts a well-formed scope payload", () => {
  assert.equal(
    isValidScopeExecuteMessage({
      task_id: "t1",
      scope: "dy_post",
      max_items_per_scope: 300,
      max_scroll_rounds: 15,
      max_stagnant_scroll_rounds: 5,
    }),
    true,
  );
});

test("isValidScopeExecuteMessage rejects malformed input", () => {
  assert.equal(isValidScopeExecuteMessage(null), false);
  assert.equal(isValidScopeExecuteMessage("string"), false);
  assert.equal(isValidScopeExecuteMessage({}), false);
  // Missing task_id
  assert.equal(
    isValidScopeExecuteMessage({
      scope: "dy_post",
      max_items_per_scope: 300,
      max_scroll_rounds: 15,
      max_stagnant_scroll_rounds: 5,
    }),
    false,
  );
  // Unknown scope
  assert.equal(
    isValidScopeExecuteMessage({
      task_id: "t",
      scope: "unknown",
      max_items_per_scope: 300,
      max_scroll_rounds: 15,
      max_stagnant_scroll_rounds: 5,
    }),
    false,
  );
  // Wrong type for numeric field
  assert.equal(
    isValidScopeExecuteMessage({
      task_id: "t",
      scope: "dy_collect",
      max_items_per_scope: "300",
      max_scroll_rounds: 15,
      max_stagnant_scroll_rounds: 5,
    }),
    false,
  );
});

test("isValidScopeExecuteMessage accepts all four scopes", () => {
  for (const scope of ["dy_post", "dy_collect", "dy_like", "dy_follow"] as const) {
    assert.equal(
      isValidScopeExecuteMessage({
        task_id: "t",
        scope,
        max_items_per_scope: 1,
        max_scroll_rounds: 0,
        max_stagnant_scroll_rounds: 0,
      }),
      true,
      `expected scope=${scope} to validate`,
    );
  }
});

test("isValidFeedExecuteMessage accepts feed payload and rejects malformed input", () => {
  assert.equal(
    isValidFeedExecuteMessage({
      task_id: "feed-1",
      max_items: 10,
    }),
    true,
  );
  assert.equal(isValidFeedExecuteMessage(null), false);
  assert.equal(isValidFeedExecuteMessage({ task_id: "", max_items: 10 }), false);
  assert.equal(isValidFeedExecuteMessage({ task_id: "feed-1", max_items: 0 }), false);
});

test("douyin discovery execution policy is dom first", () => {
  assert.deepEqual(douyinDiscoveryExecutionPolicy(), {
    search: { activeApiBridge: true, passiveFetchTap: true, domInteraction: true },
    hot: { activeApiBridge: true, passiveFetchTap: true, domInteraction: true },
    feed: { activeApiBridge: false, passiveFetchTap: true, domInteraction: true },
  });
});

test("isDouyinSearchResultUrl requires a real search results route", () => {
  assert.equal(
    isDouyinSearchResultUrl(
      "https://www.douyin.com/jingxuan/search/%E7%A7%91%E6%8A%80?enter_from=discover",
      "科技",
    ),
    true,
  );
  assert.equal(isDouyinSearchResultUrl("https://www.douyin.com/jingxuan", "科技"), false);
  assert.equal(
    isDouyinSearchResultUrl("https://www.douyin.com/jingxuan/search/%E7%BE%8E%E9%A3%9F", "科技"),
    false,
  );
  assert.equal(
    isDouyinSearchResultUrl(
      "https://www.douyin.com/jingxuan/search/%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD",
      "人工",
    ),
    false,
  );
});

test("search execute validation accepts only a boolean navigation resume flag", () => {
  const base = { task_id: "search-task", keyword: "科技", max_items: 3 };
  assert.equal(isValidSearchExecuteMessage(base), true);
  assert.equal(
    isValidSearchExecuteMessage({ ...base, resume_after_navigation: true }),
    true,
  );
  assert.equal(
    isValidSearchExecuteMessage({ ...base, resume_after_navigation: "yes" }),
    false,
  );
});

test("a new keyword discards stale search replay while navigation resume consumes it", () => {
  assert.equal(shouldReplayEarlyDiscoveryItems("dy_search", false), false);
  assert.equal(shouldReplayEarlyDiscoveryItems("dy_search", true), true);
  assert.equal(shouldReplayEarlyDiscoveryItems("dy_feed", false), true);
});

test("filterDiscoveryItemsForScope keeps only the requested discovery scope", () => {
  const items = filterDiscoveryItemsForScope(
    [
      { scope: "dy_feed", aweme_id: "feed-1", url: "", title: "feed", author: "", author_sec_uid: "", cover_url: "" },
      { scope: "dy_search", aweme_id: "search-1", url: "", title: "search", author: "", author_sec_uid: "", cover_url: "" },
      { scope: "dy_search", aweme_id: "search-1", url: "", title: "duplicate", author: "", author_sec_uid: "", cover_url: "" },
    ],
    "dy_search",
    5,
  );

  assert.equal(items.length, 1);
  assert.equal(items[0]!.scope, "dy_search");
  assert.equal(items[0]!.aweme_id, "search-1");
});

test("filterDiscoveryItemsForScope merges duplicate hot metadata", () => {
  const items = filterDiscoveryItemsForScope(
    [
      {
        scope: "dy_hot",
        aweme_id: "hot-1",
        url: "https://www.douyin.com/video/hot-1",
        title: "热点",
        author: "作者",
        author_sec_uid: "",
        cover_url: "",
        like_count: 10,
      },
      {
        scope: "dy_hot",
        aweme_id: "hot-1",
        url: "https://www.douyin.com/video/hot-1",
        title: "热点",
        author: "作者",
        author_sec_uid: "",
        cover_url: "",
        hot_word: "热点词",
        sentence_id: "2495363",
        seed_aweme_id: "7652229189183427849",
        like_count: 20,
      },
    ],
    "dy_hot",
    1,
  );

  assert.equal(items.length, 1);
  assert.equal(items[0]!.hot_word, "热点词");
  assert.equal(items[0]!.sentence_id, "2495363");
  assert.equal(items[0]!.seed_aweme_id, "7652229189183427849");
  assert.equal(items[0]!.like_count, 10);
});

test("createScrollRoundController continues while the item count grows", () => {
  const controller = createScrollRoundController({
    roundCap: 10,
    stagnantLimit: 2,
    maxItems: 100,
  });

  assert.equal(controller.shouldContinue(0), true);
  assert.equal(controller.shouldContinue(5), true);
  assert.equal(controller.shouldContinue(9), true);
  assert.equal(controller.roundsExecuted(), 3);
});

test("createScrollRoundController stops after two consecutive stagnant rounds", () => {
  const controller = createScrollRoundController({
    roundCap: 10,
    stagnantLimit: 2,
    maxItems: 100,
  });

  assert.equal(controller.shouldContinue(3), true);
  assert.equal(controller.shouldContinue(3), true); // 1st stagnant round
  assert.equal(controller.shouldContinue(3), false); // 2nd stagnant round → stop
  assert.equal(controller.roundsExecuted(), 2);
});

test("createScrollRoundController resets the stagnant streak on growth", () => {
  const controller = createScrollRoundController({
    roundCap: 10,
    stagnantLimit: 2,
    maxItems: 100,
  });

  assert.equal(controller.shouldContinue(3), true);
  assert.equal(controller.shouldContinue(3), true); // stagnant once
  assert.equal(controller.shouldContinue(7), true); // growth resets streak
  assert.equal(controller.shouldContinue(7), true); // stagnant once again
  assert.equal(controller.shouldContinue(7), false); // stagnant twice → stop
});

test("createScrollRoundController stops at the round cap", () => {
  const controller = createScrollRoundController({
    roundCap: 3,
    stagnantLimit: 2,
    maxItems: 100,
  });

  assert.equal(controller.shouldContinue(1), true);
  assert.equal(controller.shouldContinue(2), true);
  assert.equal(controller.shouldContinue(3), true);
  assert.equal(controller.shouldContinue(4), false); // cap reached
  assert.equal(controller.roundsExecuted(), 3);
});

test("createScrollRoundController stops once maxItems is reached", () => {
  const controller = createScrollRoundController({
    roundCap: 10,
    stagnantLimit: 2,
    maxItems: 5,
  });

  assert.equal(controller.shouldContinue(2), true);
  assert.equal(controller.shouldContinue(5), false);
  assert.equal(controller.roundsExecuted(), 1);
});
