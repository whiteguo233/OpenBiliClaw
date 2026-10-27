/**
 * Tests for the Douyin MAIN-world fetch-tap.
 *
 * Task 3 of the Douyin bootstrap import plan
 * (docs/plans/2026-05-06-douyin-bootstrap-import.md). The module
 * auto-install guard is inert under node:test because no browser Window
 * exists; the installers are called explicitly in these tests.
 *
 * Empirical signing / endpoint behaviour was verified against a real
 * douyin.com tab on 2026-05-07 via the chrome-devtools MCP. The
 * URL-classification regex, top-level response keys, and the late-
 * inject timing model all come from that probe — see
 * docs/plans/2026-05-06-douyin-bootstrap-import-design.md §3 step 5.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  autoInstallDouyinMainBridge,
  classifyDouyinResponseUrl,
  extractDouyinSelfSecUid,
  fetchDouyinSelfSecUid,
  harvestScopeViaApi,
  installFetchTap,
  installXhrTap,
  installApiHarvester,
  isSameWindowSameOriginApiRequest,
  parseFeedAwemeResponse,
  parseRelatedAwemeResponse,
  parseSearchAwemeResponse,
  parseAwemeListResponse,
  parseUserFollowListResponse,
  waitForDouyinSdk,
} from "../src/main/dy-fetch-tap.ts";

function bridgeMessage(
  target: { location: { origin: string } },
  data: unknown,
  overrides: { origin?: string; source?: unknown } = {},
): MessageEvent {
  const event = new MessageEvent("message", {
    data,
    origin: overrides.origin ?? target.location.origin,
  });
  // Node's MessageEvent constructor only accepts MessagePort for `source`,
  // while browsers expose the posting Window. Define the browser-shaped
  // value directly for these bridge tests.
  Object.defineProperty(event, "source", {
    value: overrides.source === undefined ? target : overrides.source,
  });
  return event;
}

test("API bridge messages require the same Window and page origin", () => {
  const target = { location: { origin: "https://www.douyin.com" } };
  assert.equal(
    isSameWindowSameOriginApiRequest(
      bridgeMessage(target, { type: "request" }),
      target as unknown as Window,
    ),
    true,
  );
  assert.equal(
    isSameWindowSameOriginApiRequest(
      bridgeMessage(target, { type: "request" }, { source: {} }),
      target as unknown as Window,
    ),
    false,
  );
  assert.equal(
    isSameWindowSameOriginApiRequest(
      bridgeMessage(target, { type: "request" }, { origin: "https://attacker.example" }),
      target as unknown as Window,
    ),
    false,
  );
});

test("installApiHarvester ignores cross-window and cross-origin requests", async () => {
  let fetchCalls = 0;
  let responseCalls = 0;
  class FakeWindow extends EventTarget {
    location = { origin: "https://www.douyin.com" };
    fetch = async (): Promise<Response> => {
      fetchCalls += 1;
      return new Response(
        JSON.stringify({ status_code: 0, user: { sec_uid: "MS4wMustNotLeak" } }),
      );
    };

    postMessage(): void {
      responseCalls += 1;
    }
  }

  const fakeWindow = new FakeWindow();
  installApiHarvester(fakeWindow as unknown as Window);
  const request = {
    type: "OPENBILICLAW_DOUYIN_IDENTITY_REQUEST",
    requestId: "rejected-request",
  };
  fakeWindow.dispatchEvent(bridgeMessage(fakeWindow, request, { source: {} }));
  fakeWindow.dispatchEvent(
    bridgeMessage(fakeWindow, request, { origin: "https://attacker.example" }),
  );
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(fetchCalls, 0);
  assert.equal(responseCalls, 0);
});

test("extractDouyinSelfSecUid accepts only the authoritative logged-in response", () => {
  assert.equal(
    extractDouyinSelfSecUid({
      status_code: 0,
      user: { sec_uid: "MS4wProfileUser" },
    }),
    "MS4wProfileUser",
  );
  assert.equal(
    extractDouyinSelfSecUid({
      status_code: 8,
      user: { sec_uid: "MS4wGuestDevice" },
    }),
    "",
  );
  assert.equal(extractDouyinSelfSecUid({ status_code: 0, user: null }), "");
  assert.equal(extractDouyinSelfSecUid(null), "");
});

test("fetchDouyinSelfSecUid uses the MAIN-world credentialed profile endpoint", async () => {
  const calls: Array<{ url: string; credentials?: RequestCredentials }> = [];
  const target = {
    location: { origin: "https://www.douyin.com" },
    fetch: async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: typeof input === "string" ? input : input.toString(),
        credentials: init?.credentials,
      });
      return new Response(
        JSON.stringify({ status_code: 0, user: { sec_uid: "MS4wProfileUser" } }),
      );
    },
  } as unknown as Window;

  assert.equal(await fetchDouyinSelfSecUid(target), "MS4wProfileUser");
  assert.deepEqual(calls, [
    {
      url: "https://www.douyin.com/aweme/v1/web/user/profile/self/",
      credentials: "include",
    },
  ]);
});

test("classifyDouyinResponseUrl maps the four bootstrap endpoints to scopes", () => {
  assert.equal(
    classifyDouyinResponseUrl(
      "https://www.douyin.com/aweme/v1/web/aweme/post/?count=18&sec_user_id=abc",
    ),
    "dy_post",
  );
  assert.equal(
    classifyDouyinResponseUrl(
      "https://www.douyin.com/aweme/v1/web/aweme/favorite/?count=18&sec_user_id=abc",
    ),
    "dy_collect",
  );
  assert.equal(
    classifyDouyinResponseUrl(
      "https://www.douyin.com/aweme/v1/web/aweme/like/?count=18&sec_user_id=abc",
    ),
    "dy_like",
  );
  assert.equal(
    classifyDouyinResponseUrl(
      "https://www.douyin.com/aweme/v1/web/user/follow/list/?count=20",
    ),
    "dy_follow",
  );
});

test("classifyDouyinResponseUrl returns null for endpoints we do NOT care about", () => {
  // Negatives drawn from real /jingxuan landing-page traffic
  // (chrome-devtools MCP probe 2026-05-07).
  assert.equal(
    classifyDouyinResponseUrl("https://www.douyin.com/aweme/v2/web/module/feed/?count=20"),
    null,
  );
  assert.equal(
    classifyDouyinResponseUrl("https://www.douyin.com/aweme/v1/web/hot/search/list/"),
    null,
  );
  assert.equal(
    classifyDouyinResponseUrl("https://www.douyin.com/aweme/v1/web/social/count?source=6"),
    null,
  );
  assert.equal(
    classifyDouyinResponseUrl("https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=x"),
    null,
  );
  assert.equal(classifyDouyinResponseUrl(""), null);
  assert.equal(classifyDouyinResponseUrl("https://example.com/"), null);
});

test("parseAwemeListResponse extracts aweme_id, desc, author, cover for dy_post", () => {
  const items = parseAwemeListResponse(
    {
      aweme_list: [
        {
          aweme_id: "111",
          create_time: 1783492200,
          desc: "demo description",
          author: { nickname: "u", sec_uid: "s" },
          video: { cover: { url_list: ["https://c1", "https://c2"] } },
          duration: 18000,
        },
      ],
    },
    "dy_post",
  );
  assert.equal(items.length, 1);
  assert.equal(items[0]!.scope, "dy_post");
  assert.equal(items[0]!.aweme_id, "111");
  assert.equal(items[0]!.title, "demo description");
  assert.equal(items[0]!.author, "u");
  assert.equal(items[0]!.author_sec_uid, "s");
  assert.equal(items[0]!.cover_url, "https://c1");
  assert.equal(items[0]!.url, "https://www.douyin.com/video/111");
  assert.equal(items[0]!.published_at, 1783492200);
});

test("parseAwemeListResponse falls back to preview_title when desc is empty", () => {
  // Real /aweme/v2/web/module/feed/ samples shipped preview_title
  // alongside a blank desc — accept both.
  const items = parseAwemeListResponse(
    {
      aweme_list: [
        {
          aweme_id: "222",
          desc: "",
          preview_title: "回退标题",
          author: { nickname: "u" },
        },
      ],
    },
    "dy_collect",
  );
  assert.equal(items[0]!.title, "回退标题");
  assert.equal("published_at" in items[0]!, false);
});

test("parseAwemeListResponse drops items with no aweme_id and no title", () => {
  const items = parseAwemeListResponse(
    {
      aweme_list: [
        { aweme_id: "", desc: "" },
        { aweme_id: "333", desc: "ok" },
        null,
        "garbage",
      ],
    },
    "dy_like",
  );
  assert.equal(items.length, 1);
  assert.equal(items[0]!.aweme_id, "333");
});

test("parseAwemeListResponse tolerates missing aweme_list / wrong types", () => {
  assert.deepEqual(parseAwemeListResponse({}, "dy_post"), []);
  assert.deepEqual(parseAwemeListResponse(null, "dy_post"), []);
  assert.deepEqual(parseAwemeListResponse({ aweme_list: "string" }, "dy_post"), []);
});

test("parseUserFollowListResponse extracts creator_sec_uid + nickname", () => {
  // Shape from f2 fetch_user_following_list reference. Top-level key
  // varies (followings vs follow_list) — accept both.
  const items = parseUserFollowListResponse({
    followings: [
      { sec_uid: "abc", nickname: "@老白", avatar_thumb: { url_list: ["https://a1"] } },
      { sec_uid: "def", nickname: "另一位" },
    ],
  });
  assert.equal(items.length, 2);
  assert.equal(items[0]!.scope, "dy_follow");
  assert.equal(items[0]!.creator_sec_uid, "abc");
  assert.equal(items[0]!.title, "@老白");
  assert.equal(items[0]!.url, "https://www.douyin.com/user/abc");
});

test("parseUserFollowListResponse accepts follow_list as alternate key", () => {
  const items = parseUserFollowListResponse({
    follow_list: [{ sec_uid: "ggg", nickname: "x" }],
  });
  assert.equal(items.length, 1);
  assert.equal(items[0]!.creator_sec_uid, "ggg");
});

test("parseUserFollowListResponse drops rows with no sec_uid", () => {
  const items = parseUserFollowListResponse({
    followings: [{ nickname: "no-uid" }, { sec_uid: "y", nickname: "ok" }],
  });
  assert.equal(items.length, 1);
  assert.equal(items[0]!.creator_sec_uid, "y");
});

test("parseSearchAwemeResponse extracts aweme_info rows from general search", () => {
  const items = parseSearchAwemeResponse({
    data: [
      {
        aweme_info: {
          aweme_id: "search-1",
          create_time: 1783492200,
          desc: "搜索结果 1",
          author: { nickname: "作者", sec_uid: "MS4wAuthor" },
          video: { cover: { url_list: ["https://cover"] } },
        },
      },
      { type: 999, card_info: { title: "not aweme" } },
    ],
  });
  assert.equal(items.length, 1);
  assert.equal(items[0]!.scope, "dy_search");
  assert.equal(items[0]!.aweme_id, "search-1");
  assert.equal(items[0]!.title, "搜索结果 1");
  assert.equal(items[0]!.author, "作者");
  assert.equal(items[0]!.author_sec_uid, "MS4wAuthor");
  assert.equal(items[0]!.cover_url, "https://cover");
  assert.equal(items[0]!.published_at, 1783492200);
});

test("parseSearchAwemeResponse accepts aweme_list from video search endpoint", () => {
  const items = parseSearchAwemeResponse({
    aweme_list: [{ aweme_id: "search-2", preview_title: "视频搜索 2" }],
  });
  assert.equal(items.length, 1);
  assert.equal(items[0]!.scope, "dy_search");
  assert.equal(items[0]!.aweme_id, "search-2");
  assert.equal(items[0]!.title, "视频搜索 2");
  assert.equal("published_at" in items[0]!, false);
});

test("parseRelatedAwemeResponse maps related aweme_list to dy_hot items", () => {
  const items = parseRelatedAwemeResponse(
    {
      aweme_list: [
        {
          aweme_id: "related-1",
          desc: "热点相关 1",
          author: { nickname: "作者", sec_uid: "MS4wAuthor" },
          video: { cover: { url_list: ["https://cover.example/related.jpg"] } },
        },
      ],
    },
    { word: "热点词", sentenceId: "2495363", seedAwemeId: "seed-1" },
  );

  assert.equal(items.length, 1);
  assert.equal(items[0]!.scope, "dy_hot");
  assert.equal(items[0]!.aweme_id, "related-1");
  assert.equal(items[0]!.title, "热点相关 1");
  assert.equal(items[0]!.cover_url, "https://cover.example/related.jpg");
  assert.equal(items[0]!.hot_word, "热点词");
  assert.equal(items[0]!.sentence_id, "2495363");
  assert.equal(items[0]!.seed_aweme_id, "seed-1");
});

test("parseFeedAwemeResponse maps tab feed aweme_list to dy_feed items", () => {
  const items = parseFeedAwemeResponse({
    aweme_list: [
      {
        aweme_id: "feed-1",
        desc: "首页推荐 1",
        author: { nickname: "推荐作者", sec_uid: "MS4wAuthor" },
        video: { origin_cover: { url_list: ["https://cover.example/feed.jpg"] } },
      },
    ],
  });

  assert.equal(items.length, 1);
  assert.equal(items[0]!.scope, "dy_feed");
  assert.equal(items[0]!.aweme_id, "feed-1");
  assert.equal(items[0]!.title, "首页推荐 1");
  assert.equal(items[0]!.author_sec_uid, "MS4wAuthor");
  assert.equal(items[0]!.cover_url, "https://cover.example/feed.jpg");
});

test("parseFeedAwemeResponse drops feed rows with no display metadata", () => {
  const items = parseFeedAwemeResponse({
    aweme_list: [
      { aweme_id: "blank-feed" },
      { aweme_id: "usable-feed", desc: "有标题" },
    ],
  });

  assert.equal(items.length, 1);
  assert.equal(items[0]!.aweme_id, "usable-feed");
});

test("waitForDouyinSdk resolves true when byted_acrawler appears", async () => {
  type W = { byted_acrawler?: unknown };
  const target: W = {};
  // Simulate SDK loading mid-poll.
  setTimeout(() => {
    target.byted_acrawler = { frontierSign: () => null };
  }, 30);
  const ok = await waitForDouyinSdk(target as unknown as Window, 500);
  assert.equal(ok, true);
});

test("waitForDouyinSdk resolves false when SDK never loads", async () => {
  const target = {} as Window;
  const ok = await waitForDouyinSdk(target, 100);
  assert.equal(ok, false);
});

test("installFetchTap wraps target.fetch and posts captured items via callback", async () => {
  // Build a fake Window that mimics the real-page state AFTER the SDK
  // has wrapped fetch — the production install path runs in this exact
  // order, so the wrapper-of-wrapper composition is what matters.
  const calls: { items: unknown[]; scope: string }[] = [];
  const fakeFetch = async (input: RequestInfo): Promise<Response> => {
    const url = typeof input === "string" ? input : (input as Request).url;
    if (url.includes("/aweme/v1/web/aweme/favorite/")) {
      const body = JSON.stringify({
        aweme_list: [{ aweme_id: "555", desc: "favorite item" }],
      });
      return new Response(body, { status: 200 });
    }
    return new Response("{}", { status: 200 });
  };
  const fakeWindow = { fetch: fakeFetch } as unknown as Window;

  installFetchTap(fakeWindow, (items, scope) => {
    calls.push({ items, scope });
  });

  await fakeWindow.fetch(
    "https://www.douyin.com/aweme/v1/web/aweme/favorite/?count=18&sec_user_id=abc",
  );

  assert.equal(calls.length, 1);
  assert.equal(calls[0]!.scope, "dy_collect");
  assert.equal((calls[0]!.items[0] as { aweme_id: string }).aweme_id, "555");
});

test("installFetchTap never relays passive request identity or query tokens", async () => {
  const messages: unknown[] = [];
  const fakeWindow = {
    location: { origin: "https://www.douyin.com" },
    fetch: async (): Promise<Response> => new Response("{}", { status: 200 }),
    postMessage: (data: unknown): void => {
      messages.push(data);
    },
  } as unknown as Window;
  installFetchTap(fakeWindow, () => {});

  await fakeWindow.fetch(
    "https://www.douyin.com/aweme/v1/web/tab/feed/?" +
      "sec_user_id=MS4wObservedUser&msToken=must-not-cross-the-bridge&X-Bogus=secret",
  );

  assert.deepEqual(messages, []);
});

test("installFetchTap does not invoke callback for non-bootstrap endpoints", async () => {
  let called = 0;
  const fakeFetch = async (): Promise<Response> =>
    new Response(JSON.stringify({ aweme_list: [] }), { status: 200 });
  const fakeWindow = { fetch: fakeFetch } as unknown as Window;
  installFetchTap(fakeWindow, () => {
    called += 1;
  });
  await fakeWindow.fetch("https://www.douyin.com/aweme/v2/web/module/feed/");
  await fakeWindow.fetch("https://www.douyin.com/aweme/v1/web/hot/search/list/");
  assert.equal(called, 0);
});

test("installFetchTap posts parsed search responses through optional search callback", async () => {
  const calls: { items: unknown[] }[] = [];
  const fakeFetch = async (): Promise<Response> =>
    new Response(
      JSON.stringify({
        data: [{ aweme_info: { aweme_id: "search-tap-1", desc: "搜索 tap" } }],
      }),
      { status: 200 },
    );
  const fakeWindow = { fetch: fakeFetch } as unknown as Window;
  installFetchTap(
    fakeWindow,
    () => {},
    (items) => calls.push({ items }),
  );
  await fakeWindow.fetch(
    "https://www.douyin.com/aweme/v1/web/general/search/single/?keyword=%E7%8C%AB",
  );
  assert.equal(calls.length, 1);
  assert.equal((calls[0]!.items[0] as { aweme_id: string }).aweme_id, "search-tap-1");
});

test("installFetchTap posts chunked search stream responses through optional search callback", { timeout: 1000 }, async () => {
  const calls: { items: unknown[] }[] = [];
  let captured!: () => void;
  const observed = new Promise<void>(resolve => { captured = resolve; });
  const fakeFetch = async (): Promise<Response> =>
    new Response(
      '14c0\r\n{"status_code":0,"data":[{"aweme_info":{"aweme_id":"stream-search-1","desc":"搜索 stream"}}]}',
      { status: 200, headers: { "content-type": "application/json" } },
    );
  const fakeWindow = { fetch: fakeFetch } as unknown as Window;
  installFetchTap(
    fakeWindow,
    () => {},
    (items) => { calls.push({ items }); captured(); },
  );
  await fakeWindow.fetch(
    "https://www.douyin.com/aweme/v1/web/general/search/stream/?keyword=%E7%A7%91%E6%8A%80",
  );
  await observed;
  assert.equal(calls.length, 1);
  assert.equal((calls[0]!.items[0] as { aweme_id: string }).aweme_id, "stream-search-1");
});

test("search stream headers reach the page before the body ends", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start(value) { controller = value; } });
  const response = new Response(stream);
  const target = { fetch: async () => response } as unknown as Window;
  installFetchTap(target, () => {}, () => {});
  const pending = target.fetch("https://www.douyin.com/aweme/v1/web/general/search/stream/?keyword=x");
  let timer: ReturnType<typeof setTimeout>;
  try {
    const received = await Promise.race([
      pending,
      new Promise<null>(resolve => { timer = setTimeout(() => resolve(null), 50); }),
    ]);
    assert.equal(received, response, "the tap must not delay the native stream consumer");
  } finally {
    clearTimeout(timer!);
    controller.close();
    await pending;
  }
});

test("search stream emits complete JSON frames before EOF and preserves the original body", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start(value) { controller = value; } });
  const response = new Response(stream);
  const target = { fetch: async () => response } as unknown as Window;
  const calls: string[] = [];
  let captured!: () => void;
  const gotItems = new Promise<void>(resolve => { captured = resolve; });
  installFetchTap(target, () => {}, items => {
    calls.push(...items.map(item => item.aweme_id));
    if (calls.length >= 2) captured();
  });
  const pending = target.fetch("https://www.douyin.com/aweme/v1/web/general/search/stream/?keyword=x");
  const encoder = new TextEncoder();
  const first = JSON.stringify({ status_code: 0, data: [{ aweme_info: { aweme_id: "first-frame", desc: '括号 } 和引号 " 中文' } }] });
  const second = JSON.stringify({ status_code: 0, data: [{ aweme_info: { aweme_id: "second-frame", desc: "第二帧" } }] });
  const text = `data: ${first}\n\ndata: ${second}\n\n`;
  const bytes = encoder.encode(text);
  controller.enqueue(bytes.slice(0, 19));
  controller.enqueue(bytes.slice(19, 93));
  controller.enqueue(bytes.slice(93));
  let timer: ReturnType<typeof setTimeout>;
  try {
    await Promise.race([gotItems, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("video frames must not wait for EOF")), 100);
    })]);
    assert.deepEqual(calls, ["first-frame", "second-frame"]);
  } finally {
    clearTimeout(timer!);
    controller.close();
    const original = await pending;
    assert.equal(await original.text(), text);
  }
});

test("installFetchTap passively posts feed responses through optional search callback", async () => {
  const calls: { items: unknown[]; scope: string }[] = [];
  const fakeFetch = async (): Promise<Response> =>
    new Response(
      JSON.stringify({
        aweme_list: [{ aweme_id: "feed-passive-1", desc: "首页推荐 passive" }],
      }),
      { status: 200 },
    );
  const fakeWindow = { fetch: fakeFetch } as unknown as Window;
  installFetchTap(
    fakeWindow,
    () => {},
    (items, scope) => calls.push({ items, scope }),
  );
  await fakeWindow.fetch("https://www.douyin.com/aweme/v1/web/tab/feed/?count=10");
  await fakeWindow.fetch("https://www.douyin.com/aweme/v2/web/module/feed/?count=20");
  assert.equal(calls.length, 2);
  assert.equal(calls[0]!.scope, "dy_feed");
  assert.equal((calls[0]!.items[0] as { scope: string }).scope, "dy_feed");
  assert.equal((calls[0]!.items[0] as { aweme_id: string }).aweme_id, "feed-passive-1");
  assert.equal((calls[1]!.items[0] as { scope: string }).scope, "dy_feed");
  assert.equal((calls[1]!.items[0] as { aweme_id: string }).aweme_id, "feed-passive-1");
});

test("installFetchTap reports a parsed empty feed response as observed", async () => {
  const calls: { items: unknown[]; scope: string }[] = [];
  const fakeWindow = {
    fetch: async (): Promise<Response> =>
      new Response(JSON.stringify({ status_code: 0, aweme_list: [] }), { status: 200 }),
  } as unknown as Window;
  installFetchTap(
    fakeWindow,
    () => {},
    (items, scope) => calls.push({ items, scope }),
  );

  await fakeWindow.fetch("https://www.douyin.com/aweme/v2/web/module/feed/?count=20");

  assert.deepEqual(calls, [{ items: [], scope: "dy_feed" }]);
});

test("installFetchTap does not call error or malformed feed JSON a valid observation", async () => {
  const responses = [
    new Response(JSON.stringify({ status_code: 0, aweme_list: [] }), { status: 429 }),
    new Response(JSON.stringify({ status_code: 4, aweme_list: [] }), { status: 200 }),
    new Response(JSON.stringify({ status_code: 0 }), { status: 200 }),
  ];

  for (const response of responses) {
    let observations = 0;
    const fakeWindow = {
      fetch: async (): Promise<Response> => response,
    } as unknown as Window;
    installFetchTap(
      fakeWindow,
      () => {},
      () => {
        observations += 1;
      },
    );
    await fakeWindow.fetch("https://www.douyin.com/aweme/v2/web/module/feed/");
    assert.equal(observations, 0);
  }
});

test("installXhrTap passively posts feed responses through optional search callback", () => {
  const calls: { items: unknown[] }[] = [];

  class FakeXMLHttpRequest extends EventTarget {
    readyState = 0;
    responseText = "";

    open(): void {
      // The production wrapper stores URL/listener before delegating here.
    }
  }

  const fakeWindow = { XMLHttpRequest: FakeXMLHttpRequest } as unknown as Window;
  installXhrTap(
    fakeWindow,
    () => {},
    (items) => calls.push({ items }),
  );

  const xhr = new FakeXMLHttpRequest();
  xhr.open("POST", "https://www.douyin.com/aweme/v2/web/module/feed/");
  xhr.responseText = JSON.stringify({
    aweme_list: [{ aweme_id: "feed-xhr-passive-1", desc: "首页推荐 xhr passive" }],
  });
  xhr.readyState = 4;
  xhr.dispatchEvent(new Event("readystatechange"));

  assert.equal(calls.length, 1);
  assert.equal((calls[0]!.items[0] as { scope: string }).scope, "dy_feed");
  assert.equal((calls[0]!.items[0] as { aweme_id: string }).aweme_id, "feed-xhr-passive-1");
});

test("installXhrTap rejects non-2xx feed responses as observations", () => {
  let observations = 0;

  class FakeXMLHttpRequest extends EventTarget {
    readyState = 0;
    responseText = "";
    status = 0;

    open(): void {}
  }

  const fakeWindow = { XMLHttpRequest: FakeXMLHttpRequest } as unknown as Window;
  installXhrTap(
    fakeWindow,
    () => {},
    () => {
      observations += 1;
    },
  );
  const xhr = new FakeXMLHttpRequest();
  xhr.open("GET", "https://www.douyin.com/aweme/v2/web/module/feed/");
  xhr.status = 429;
  xhr.responseText = JSON.stringify({ status_code: 0, aweme_list: [] });
  xhr.readyState = 4;
  xhr.dispatchEvent(new Event("readystatechange"));

  assert.equal(observations, 0);
});

test("installFetchTap passively posts related responses through optional search callback", async () => {
  const calls: { items: unknown[] }[] = [];
  const fakeFetch = async (): Promise<Response> =>
    new Response(
      JSON.stringify({
        aweme_list: [{ aweme_id: "hot-passive-1", desc: "热点相关 passive" }],
      }),
      { status: 200 },
    );
  const fakeWindow = { fetch: fakeFetch } as unknown as Window;
  installFetchTap(
    fakeWindow,
    () => {},
    (items) => calls.push({ items }),
  );
  await fakeWindow.fetch(
    "https://www.douyin.com/aweme/v1/web/aweme/related/?aweme_id=seed&count=10",
  );
  assert.equal(calls.length, 1);
  assert.equal((calls[0]!.items[0] as { scope: string }).scope, "dy_hot");
  assert.equal((calls[0]!.items[0] as { aweme_id: string }).aweme_id, "hot-passive-1");
});

test("installFetchTap returns the original fetch's response unchanged", async () => {
  // The page's own consumer must still see the original Response
  // body — we only clone() to read off the side. Otherwise we'd
  // disrupt React's data flow.
  const fakeFetch = async (): Promise<Response> =>
    new Response(JSON.stringify({ aweme_list: [{ aweme_id: "777" }] }), {
      status: 200,
    });
  const fakeWindow = { fetch: fakeFetch } as unknown as Window;
  installFetchTap(fakeWindow, () => {});
  const resp = await fakeWindow.fetch(
    "https://www.douyin.com/aweme/v1/web/aweme/like/?count=18",
  );
  const json = (await resp.json()) as { aweme_list: { aweme_id: string }[] };
  assert.equal(json.aweme_list[0]!.aweme_id, "777");
});

test("installFetchTap disposer restores the original fetch", async () => {
  const original = async (): Promise<Response> => new Response("{}");
  const fakeWindow = { fetch: original } as unknown as Window;
  const dispose = installFetchTap(fakeWindow, () => {});
  // After install, fetch is wrapped (a different function reference).
  assert.notEqual(fakeWindow.fetch, original);
  dispose();
  assert.equal(fakeWindow.fetch, original);
});

test("fetch and XHR taps install only one wrapper per page Window", async () => {
  const originalFetch = async (): Promise<Response> =>
    new Response(
      JSON.stringify({ aweme_list: [{ aweme_id: "once-1", desc: "only once" }] }),
    );
  let firstFetchCallback = 0;
  let duplicateFetchCallback = 0;

  class FakeXMLHttpRequest extends EventTarget {
    readyState = 0;
    responseText = "";

    open(): void {}
  }

  const fakeWindow = {
    fetch: originalFetch,
    XMLHttpRequest: FakeXMLHttpRequest,
  } as unknown as Window;
  const disposeFetch = installFetchTap(fakeWindow, () => {
    firstFetchCallback += 1;
  });
  const wrappedFetch = fakeWindow.fetch;
  const disposeDuplicateFetch = installFetchTap(fakeWindow, () => {
    duplicateFetchCallback += 1;
  });

  const disposeXhr = installXhrTap(fakeWindow, () => {
    firstFetchCallback += 1;
  });
  const wrappedOpen = FakeXMLHttpRequest.prototype.open;
  const disposeDuplicateXhr = installXhrTap(fakeWindow, () => {
    duplicateFetchCallback += 1;
  });

  assert.equal(fakeWindow.fetch, wrappedFetch);
  assert.equal(FakeXMLHttpRequest.prototype.open, wrappedOpen);
  await fakeWindow.fetch("https://www.douyin.com/aweme/v1/web/aweme/post/?count=18");

  const xhr = new FakeXMLHttpRequest();
  xhr.open("GET", "https://www.douyin.com/aweme/v1/web/aweme/post/?count=18");
  xhr.responseText = JSON.stringify({
    aweme_list: [{ aweme_id: "once-2", desc: "XHR only once" }],
  });
  xhr.readyState = 4;
  xhr.dispatchEvent(new Event("readystatechange"));

  assert.equal(firstFetchCallback, 2);
  assert.equal(duplicateFetchCallback, 0);

  // A duplicate installer's disposer is intentionally a no-op; the original
  // installation remains live until its owner disposes it.
  disposeDuplicateFetch();
  disposeDuplicateXhr();
  assert.equal(fakeWindow.fetch, wrappedFetch);
  assert.equal(FakeXMLHttpRequest.prototype.open, wrappedOpen);
  disposeFetch();
  disposeXhr();
  assert.equal(fakeWindow.fetch, originalFetch);
});

test("pending MAIN auto-install re-wraps page fetch and XHR without a second SDK waiter", () => {
  const originalFetch = async (): Promise<Response> => new Response("{}");
  const pageFetch = async (): Promise<Response> => new Response("{}");
  let messageListenerInstalls = 0;

  class FakeXMLHttpRequest extends EventTarget {
    readyState = 0;
    responseText = "";

    open(): void {}
  }

  class FakeWindow extends EventTarget {
    location = { origin: "https://www.douyin.com" };
    fetch = originalFetch;
    XMLHttpRequest = FakeXMLHttpRequest;

    override addEventListener(
      type: string,
      callback: EventListenerOrEventListenerObject | null,
      options?: AddEventListenerOptions | boolean,
    ): void {
      if (type === "message") messageListenerInstalls += 1;
      super.addEventListener(type, callback, options);
    }

    postMessage(): void {}
  }

  const fakeWindow = new FakeWindow() as unknown as Window;
  let sdkWaiters = 0;
  const neverReady = (): Promise<boolean> => {
    sdkWaiters += 1;
    return new Promise<boolean>(() => {});
  };
  autoInstallDouyinMainBridge(fakeWindow, neverReady);
  const firstWrappedFetch = fakeWindow.fetch;
  const firstWrappedOpen = FakeXMLHttpRequest.prototype.open;
  assert.notEqual(firstWrappedFetch, originalFetch);
  assert.equal(messageListenerInstalls, 1);

  (fakeWindow as unknown as { fetch: typeof pageFetch }).fetch = pageFetch;
  const pageOpen = function pageOpen(): void {};
  FakeXMLHttpRequest.prototype.open = pageOpen;
  autoInstallDouyinMainBridge(fakeWindow, neverReady);

  assert.notEqual(fakeWindow.fetch, pageFetch);
  assert.notEqual(FakeXMLHttpRequest.prototype.open, pageOpen);
  assert.notEqual(fakeWindow.fetch, firstWrappedFetch);
  assert.notEqual(FakeXMLHttpRequest.prototype.open, firstWrappedOpen);
  assert.equal(messageListenerInstalls, 1);
  assert.equal(sdkWaiters, 1);
});

test("installApiHarvester resolves self identity through the postMessage bridge", async () => {
  class FakeWindow extends EventTarget {
    location = { origin: "https://www.douyin.com" };
    fetch = async (): Promise<Response> =>
      new Response(
        JSON.stringify({ status_code: 0, user: { sec_uid: "MS4wBridgeUser" } }),
      );

    postMessage(data: unknown): void {
      queueMicrotask(() => {
        this.dispatchEvent(bridgeMessage(this, data));
      });
    }
  }

  const fakeWindow = new FakeWindow();
  installApiHarvester(fakeWindow as unknown as Window);
  const response = await new Promise<Record<string, unknown>>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("identity response timeout")), 500);
    fakeWindow.addEventListener("message", function onMessage(event) {
      const data = (event as MessageEvent).data as Record<string, unknown>;
      if (data?.type !== "OPENBILICLAW_DOUYIN_IDENTITY_RESPONSE") return;
      clearTimeout(timer);
      fakeWindow.removeEventListener("message", onMessage);
      resolve(data);
    });
    fakeWindow.dispatchEvent(
      bridgeMessage(fakeWindow, {
          type: "OPENBILICLAW_DOUYIN_IDENTITY_REQUEST",
          requestId: "identity-1",
      }),
    );
  });

  assert.equal(response.requestId, "identity-1");
  assert.equal(response.secUid, "MS4wBridgeUser");
  assert.equal(response.error, undefined);
});

test("installApiHarvester deduplicates concurrent self identity requests per tab", async () => {
  let fetchCalls = 0;
  class FakeWindow extends EventTarget {
    location = { origin: "https://www.douyin.com" };
    fetch = async (): Promise<Response> => {
      fetchCalls += 1;
      await new Promise((resolve) => setTimeout(resolve, 10));
      return new Response(
        JSON.stringify({ status_code: 0, user: { sec_uid: "MS4wSharedUser" } }),
      );
    };

    postMessage(data: unknown): void {
      queueMicrotask(() => {
        this.dispatchEvent(bridgeMessage(this, data));
      });
    }
  }

  const fakeWindow = new FakeWindow();
  installApiHarvester(fakeWindow as unknown as Window);
  const responses = new Promise<Record<string, string>[]>((resolve, reject) => {
    const received: Record<string, string>[] = [];
    const timer = setTimeout(() => reject(new Error("identity responses timeout")), 500);
    fakeWindow.addEventListener("message", function onMessage(event) {
      const data = (event as MessageEvent).data as Record<string, string>;
      if (data?.type !== "OPENBILICLAW_DOUYIN_IDENTITY_RESPONSE") return;
      received.push(data);
      if (received.length < 2) return;
      clearTimeout(timer);
      fakeWindow.removeEventListener("message", onMessage);
      resolve(received);
    });
  });

  for (const requestId of ["identity-a", "identity-b"]) {
    fakeWindow.dispatchEvent(
      bridgeMessage(fakeWindow, {
          type: "OPENBILICLAW_DOUYIN_IDENTITY_REQUEST",
          requestId,
      }),
    );
  }

  const results = await responses;
  assert.equal(fetchCalls, 1);
  assert.deepEqual(
    results.map((result) => [result.requestId, result.secUid]).sort(),
    [
      ["identity-a", "MS4wSharedUser"],
      ["identity-b", "MS4wSharedUser"],
    ],
  );
});

test("installApiHarvester is idempotent and runs a duplicate requestId only once", async () => {
  let listenerInstalls = 0;
  let fetchCalls = 0;
  const fetchResolvers: Array<(response: Response) => void> = [];

  class FakeWindow extends EventTarget {
    location = { origin: "https://www.douyin.com" };
    fetch = async (): Promise<Response> => {
      fetchCalls += 1;
      return await new Promise<Response>((resolve) => fetchResolvers.push(resolve));
    };

    override addEventListener(
      type: string,
      callback: EventListenerOrEventListenerObject | null,
      options?: AddEventListenerOptions | boolean,
    ): void {
      if (type === "message") listenerInstalls += 1;
      super.addEventListener(type, callback, options);
    }

    postMessage(data: unknown): void {
      queueMicrotask(() => {
        this.dispatchEvent(bridgeMessage(this, data));
      });
    }
  }

  const fakeWindow = new FakeWindow();
  installApiHarvester(fakeWindow as unknown as Window);
  installApiHarvester(fakeWindow as unknown as Window);
  assert.equal(listenerInstalls, 1);

  const request = {
    type: "OPENBILICLAW_DOUYIN_FEED_API_REQUEST",
    requestId: "same-request-id",
    maxItems: 5,
  };
  const firstResponse = new Promise<Record<string, unknown>>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("feed response timeout")), 500);
    fakeWindow.addEventListener("message", function onMessage(event) {
      const data = (event as MessageEvent).data as Record<string, unknown>;
      if (data?.type !== "OPENBILICLAW_DOUYIN_FEED_API_RESPONSE") return;
      clearTimeout(timer);
      fakeWindow.removeEventListener("message", onMessage);
      resolve(data);
    });
  });

  fakeWindow.dispatchEvent(bridgeMessage(fakeWindow, request));
  fakeWindow.dispatchEvent(bridgeMessage(fakeWindow, request));
  assert.equal(fetchCalls, 1);
  fetchResolvers.shift()!(
    new Response(
      JSON.stringify({
        status_code: 0,
        aweme_list: [{ aweme_id: "deduped-feed", desc: "deduped" }],
      }),
    ),
  );

  const response = await firstResponse;
  assert.equal(fetchCalls, 1);
  assert.equal((response.items as { aweme_id: string }[])[0]!.aweme_id, "deduped-feed");

  // Settled IDs are released, so a genuinely new request can reuse the same
  // correlation id without the registry growing forever.
  await new Promise((resolve) => setTimeout(resolve, 0));
  fakeWindow.dispatchEvent(bridgeMessage(fakeWindow, request));
  assert.equal(fetchCalls, 2);
  fetchResolvers.shift()!(new Response(JSON.stringify({ status_code: 0, aweme_list: [] })));
  await new Promise((resolve) => setTimeout(resolve, 0));
});

test("installApiHarvester evicts expired requestIds and suppresses their late response", async () => {
  let fetchCalls = 0;
  const fetchResolvers: Array<(response: Response) => void> = [];

  class FakeWindow extends EventTarget {
    location = { origin: "https://www.douyin.com" };
    fetch = async (): Promise<Response> => {
      fetchCalls += 1;
      return await new Promise<Response>((resolve) => fetchResolvers.push(resolve));
    };

    postMessage(data: unknown): void {
      queueMicrotask(() => {
        this.dispatchEvent(bridgeMessage(this, data));
      });
    }
  }

  const fakeWindow = new FakeWindow();
  installApiHarvester(fakeWindow as unknown as Window, 5);
  const request = {
    type: "OPENBILICLAW_DOUYIN_FEED_API_REQUEST",
    requestId: "expired-request-id",
    maxItems: 5,
  };

  fakeWindow.dispatchEvent(bridgeMessage(fakeWindow, request));
  assert.equal(fetchCalls, 1);
  await new Promise((resolve) => setTimeout(resolve, 20));

  const response = new Promise<Record<string, unknown>>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("replacement feed response timeout")), 500);
    fakeWindow.addEventListener("message", function onMessage(event) {
      const data = (event as MessageEvent).data as Record<string, unknown>;
      if (data?.type !== "OPENBILICLAW_DOUYIN_FEED_API_RESPONSE") return;
      clearTimeout(timer);
      fakeWindow.removeEventListener("message", onMessage);
      resolve(data);
    });
  });
  fakeWindow.dispatchEvent(bridgeMessage(fakeWindow, request));
  assert.equal(fetchCalls, 2);

  const expiredResolver = fetchResolvers.shift()!;
  const replacementResolver = fetchResolvers.shift()!;
  expiredResolver(
    new Response(
      JSON.stringify({ aweme_list: [{ aweme_id: "stale-feed", desc: "stale" }] }),
    ),
  );
  replacementResolver(
    new Response(
      JSON.stringify({ aweme_list: [{ aweme_id: "fresh-feed", desc: "fresh" }] }),
    ),
  );

  const result = await response;
  assert.equal((result.items as { aweme_id: string }[])[0]!.aweme_id, "fresh-feed");
});

test("installApiHarvester paginates favorite and like scopes through the postMessage bridge", async () => {
  const fetchCalls: { url: string; credentials?: RequestCredentials }[] = [];
  const favoritePages = new Map<number, unknown>([
    [
      0,
      {
        has_more: true,
        max_cursor: "000123",
        aweme_list: [
          { aweme_id: "fav-1", desc: "收藏 1" },
          { aweme_id: "fav-1", desc: "duplicate" },
        ],
      },
    ],
    [
      123,
      {
        has_more: false,
        max_cursor: 0,
        aweme_list: [{ aweme_id: "fav-2", preview_title: "收藏 2" }],
      },
    ],
  ]);

  const fakeFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    fetchCalls.push({ url, credentials: init?.credentials });
    const parsed = new URL(url, "https://www.douyin.com");
    if (parsed.pathname.includes("/aweme/v1/web/aweme/favorite/")) {
      const cursor = Number(parsed.searchParams.get("max_cursor") ?? 0);
      return new Response(JSON.stringify(favoritePages.get(cursor) ?? { aweme_list: [] }));
    }
    if (parsed.pathname.includes("/aweme/v1/web/aweme/like/")) {
      return new Response(
        JSON.stringify({
          has_more: false,
          max_cursor: 0,
          aweme_list: [{ aweme_id: "like-1", desc: "点赞 1" }],
        }),
      );
    }
    return new Response("{}", { status: 404 });
  };

  class FakeWindow extends EventTarget {
    fetch = fakeFetch;
    location = { origin: "https://www.douyin.com" };

    postMessage(data: unknown): void {
      queueMicrotask(() => {
        this.dispatchEvent(bridgeMessage(this, data));
      });
    }
  }

  const fakeWindow = new FakeWindow();
  installApiHarvester(fakeWindow as unknown as Window);

  async function requestScope(scope: "dy_collect" | "dy_like") {
    const requestId = `req-${scope}`;
    return await new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("api harvester response timeout")), 500);
      fakeWindow.addEventListener("message", function onMessage(event) {
        const data = (event as MessageEvent).data as Record<string, unknown>;
        if (data?.type !== "OPENBILICLAW_DOUYIN_API_RESPONSE") return;
        if (data.requestId !== requestId) return;
        clearTimeout(timer);
        fakeWindow.removeEventListener("message", onMessage);
        resolve(data);
      });
      fakeWindow.dispatchEvent(
        bridgeMessage(fakeWindow, {
            type: "OPENBILICLAW_DOUYIN_API_REQUEST",
            requestId,
            scope,
            secUid: "MS4wTestUser",
            maxItems: 10,
        }),
      );
    });
  }

  const favoriteResult = await requestScope("dy_collect");
  const likeResult = await requestScope("dy_like");

  assert.equal(favoriteResult.pages_fetched, 2);
  assert.equal(likeResult.pages_fetched, 1);
  assert.deepEqual(
    (favoriteResult.items as { aweme_id: string; scope: string; title: string }[]).map(
      (item) => [item.scope, item.aweme_id, item.title],
    ),
    [
      ["dy_collect", "fav-1", "收藏 1"],
      ["dy_collect", "fav-2", "收藏 2"],
    ],
  );
  assert.deepEqual(
    (likeResult.items as { aweme_id: string; scope: string; title: string }[]).map(
      (item) => [item.scope, item.aweme_id, item.title],
    ),
    [["dy_like", "like-1", "点赞 1"]],
  );
  assert.equal(fetchCalls.every((call) => call.credentials === "include"), true);
  assert.equal(fetchCalls[0]!.url.includes("/aweme/v1/web/aweme/favorite/"), true);
  assert.equal(fetchCalls[0]!.url.includes("sec_user_id=MS4wTestUser"), true);
  assert.equal(fetchCalls[1]!.url.includes("max_cursor=123"), true);
  assert.equal(fetchCalls[2]!.url.includes("/aweme/v1/web/aweme/like/"), true);
});

test("harvestScopeViaApi accepts normalized follow cursor aliases", async () => {
  const cases: Array<{
    field: "min_time" | "max_time" | "cursor";
    value: number | string;
    expected: string;
  }> = [
    { field: "min_time", value: 101, expected: "101" },
    { field: "max_time", value: "000102", expected: "102" },
    { field: "cursor", value: "103", expected: "103" },
  ];

  for (const cursorCase of cases) {
    const fetchCalls: string[] = [];
    const fakeWindow = {
      fetch: async (input: RequestInfo | URL): Promise<Response> => {
        const url = typeof input === "string" ? input : input.toString();
        fetchCalls.push(url);
        if (fetchCalls.length === 1) {
          return new Response(
            JSON.stringify({
              has_more: true,
              [cursorCase.field]: cursorCase.value,
              followings: [{ sec_uid: `follow-${cursorCase.field}`, nickname: "关注" }],
            }),
          );
        }
        return new Response(JSON.stringify({ has_more: false, followings: [] }));
      },
    } as unknown as Window;

    const result = await harvestScopeViaApi(
      fakeWindow,
      "dy_follow",
      "MS4wTestUser",
      10,
      0,
    );

    assert.equal(result.error, undefined);
    assert.equal(result.pages_fetched, 2);
    assert.equal(fetchCalls[1]!.includes(`max_time=${cursorCase.expected}`), true);
  }
});

test("harvestScopeViaApi normalizes string has_more flags", async () => {
  const pages = [
    {
      has_more: "1",
      max_cursor: "001",
      aweme_list: [{ aweme_id: "string-flag-1" }],
    },
    {
      has_more: "0",
      aweme_list: [{ aweme_id: "string-flag-2" }],
    },
  ];
  let fetchCalls = 0;
  const fakeWindow = {
    fetch: async (): Promise<Response> => {
      const payload = pages[fetchCalls]!;
      fetchCalls += 1;
      return new Response(JSON.stringify(payload));
    },
  } as unknown as Window;

  const result = await harvestScopeViaApi(
    fakeWindow,
    "dy_collect",
    "MS4wTestUser",
    10,
    0,
  );

  assert.equal(result.error, undefined);
  assert.equal(result.pages_fetched, 2);
  assert.equal(fetchCalls, 2);
});

test("harvestScopeViaApi reports a non-zero API business status", async () => {
  const fakeWindow = {
    fetch: async (): Promise<Response> =>
      new Response(
        JSON.stringify({
          status_code: 8,
          status_msg: "not logged in",
          has_more: false,
          aweme_list: [{ aweme_id: "must-not-accept" }],
        }),
      ),
  } as unknown as Window;

  const result = await harvestScopeViaApi(
    fakeWindow,
    "dy_collect",
    "MS4wTestUser",
    10,
    0,
  );

  assert.equal(result.error, "api_status_8");
  assert.equal(result.pages_fetched, 1);
  assert.deepEqual(result.items, []);
});

test("harvestScopeViaApi rejects missing/invalid has_more and non-object responses", async () => {
  const cases: Array<{
    name: string;
    payload: unknown;
    expectedError: string;
    expectedItems: number;
  }> = [
    {
      name: "missing has_more",
      payload: {
        status_code: 0,
        aweme_list: [{ aweme_id: "missing-has-more" }],
      },
      expectedError: "pagination_has_more_missing",
      expectedItems: 1,
    },
    {
      name: "null has_more",
      payload: {
        status_code: 0,
        has_more: null,
        aweme_list: [{ aweme_id: "null-has-more" }],
      },
      expectedError: "pagination_has_more_invalid",
      expectedItems: 1,
    },
    {
      name: "null response",
      payload: null,
      expectedError: "api_response_invalid",
      expectedItems: 0,
    },
    {
      name: "array response",
      payload: [],
      expectedError: "api_response_invalid",
      expectedItems: 0,
    },
  ];

  for (const responseCase of cases) {
    const fakeWindow = {
      fetch: async (): Promise<Response> =>
        new Response(JSON.stringify(responseCase.payload)),
    } as unknown as Window;

    const result = await harvestScopeViaApi(
      fakeWindow,
      "dy_collect",
      "MS4wTestUser",
      10,
      0,
    );

    assert.equal(result.error, responseCase.expectedError, responseCase.name);
    assert.equal(result.pages_fetched, 1, responseCase.name);
    assert.equal(result.items.length, responseCase.expectedItems, responseCase.name);
  }
});

test("harvestScopeViaApi reports missing, invalid, stalled, and cyclic cursors", async () => {
  const cases: Array<{
    name: string;
    pages: Array<Record<string, unknown>>;
    expectedError: string;
    expectedPages: number;
  }> = [
    {
      name: "missing",
      pages: [{ has_more: true, aweme_list: [{ aweme_id: "missing-1" }] }],
      expectedError: "pagination_cursor_missing",
      expectedPages: 1,
    },
    {
      name: "invalid string",
      pages: [
        {
          has_more: true,
          max_cursor: "not-a-cursor",
          aweme_list: [{ aweme_id: "invalid-1" }],
        },
      ],
      expectedError: "pagination_cursor_invalid",
      expectedPages: 1,
    },
    {
      name: "invalid has_more",
      pages: [
        {
          has_more: "maybe",
          max_cursor: "1",
          aweme_list: [{ aweme_id: "invalid-has-more-1" }],
        },
      ],
      expectedError: "pagination_has_more_invalid",
      expectedPages: 1,
    },
    {
      name: "not advanced after normalization",
      pages: [
        {
          has_more: true,
          max_cursor: "000",
          aweme_list: [{ aweme_id: "stalled-1" }],
        },
      ],
      expectedError: "pagination_cursor_not_advanced",
      expectedPages: 1,
    },
    {
      name: "cycle",
      pages: [
        {
          has_more: true,
          max_cursor: "1",
          aweme_list: [{ aweme_id: "cycle-1" }],
        },
        {
          has_more: true,
          max_cursor: 0,
          aweme_list: [{ aweme_id: "cycle-2" }],
        },
      ],
      expectedError: "pagination_cursor_cycle",
      expectedPages: 2,
    },
  ];

  for (const cursorCase of cases) {
    let fetchCalls = 0;
    const fakeWindow = {
      fetch: async (): Promise<Response> => {
        const payload = cursorCase.pages[Math.min(fetchCalls, cursorCase.pages.length - 1)]!;
        fetchCalls += 1;
        return new Response(JSON.stringify(payload));
      },
    } as unknown as Window;

    const result = await harvestScopeViaApi(
      fakeWindow,
      "dy_collect",
      "MS4wTestUser",
      10,
      0,
    );

    assert.equal(result.error, cursorCase.expectedError, cursorCase.name);
    assert.equal(result.pages_fetched, cursorCase.expectedPages, cursorCase.name);
    assert.equal(result.items.length, cursorCase.expectedPages, cursorCase.name);
  }
});

test("harvestScopeViaApi reports a still-open cursor at the page safety cap", async () => {
  let fetchCalls = 0;
  const fakeWindow = {
    fetch: async (): Promise<Response> => {
      fetchCalls += 1;
      return new Response(
        JSON.stringify({
          has_more: true,
          max_cursor: String(fetchCalls),
          aweme_list: [],
        }),
      );
    },
  } as unknown as Window;

  const result = await harvestScopeViaApi(
    fakeWindow,
    "dy_collect",
    "MS4wTestUser",
    1,
    0,
  );

  assert.equal(fetchCalls, 50);
  assert.equal(result.pages_fetched, 50);
  assert.equal(result.error, "pagination_page_limit_reached");
});

test("installApiHarvester surfaces a late pagination failure with harvested items", async () => {
  let fetchCalls = 0;
  class FakeWindow extends EventTarget {
    location = { origin: "https://www.douyin.com" };
    fetch = async (): Promise<Response> => {
      fetchCalls += 1;
      if (fetchCalls === 1) {
        return new Response(
          JSON.stringify({
            has_more: true,
            max_cursor: 99,
            aweme_list: [{ aweme_id: "fav-partial", desc: "已采集" }],
          }),
        );
      }
      return new Response("risk controlled", { status: 429 });
    };

    postMessage(data: unknown): void {
      queueMicrotask(() => {
        this.dispatchEvent(bridgeMessage(this, data));
      });
    }
  }

  const fakeWindow = new FakeWindow();
  installApiHarvester(fakeWindow as unknown as Window);
  const result = await new Promise<Record<string, unknown>>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("api harvester response timeout")), 1_000);
    fakeWindow.addEventListener("message", function onMessage(event) {
      const data = (event as MessageEvent).data as Record<string, unknown>;
      if (data?.type !== "OPENBILICLAW_DOUYIN_API_RESPONSE") return;
      if (data.requestId !== "late-failure") return;
      clearTimeout(timer);
      fakeWindow.removeEventListener("message", onMessage);
      resolve(data);
    });
    fakeWindow.dispatchEvent(
      bridgeMessage(fakeWindow, {
          type: "OPENBILICLAW_DOUYIN_API_REQUEST",
          requestId: "late-failure",
          scope: "dy_collect",
          secUid: "MS4wTestUser",
          maxItems: 10,
      }),
    );
  });

  assert.equal(result.pages_fetched, 1);
  assert.equal(result.error, "HTTP 429");
  assert.deepEqual(
    (result.items as { aweme_id: string }[]).map((item) => item.aweme_id),
    ["fav-partial"],
  );
});

test("installApiHarvester signs douyin search URLs with the page acrawler", async () => {
  const fetchCalls: { url: string; credentials?: RequestCredentials }[] = [];
  const fakeFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    fetchCalls.push({ url, credentials: init?.credentials });
    return new Response(
      JSON.stringify({
        has_more: false,
        cursor: 0,
        data: [{ aweme_info: { aweme_id: "signed-search-1", desc: "签名搜索" } }],
      }),
    );
  };

  class FakeWindow extends EventTarget {
    fetch = fakeFetch;
    location = { origin: "https://www.douyin.com" };
    byted_acrawler = {
      frontierSign({ url }: { url: string }) {
        assert.equal(url.includes("search_channel=aweme_video_web"), true);
        assert.equal(url.includes("screen_width=1920"), true);
        return { "X-Bogus": "signed-xbogus" };
      },
    };

    postMessage(data: unknown): void {
      queueMicrotask(() => {
        this.dispatchEvent(bridgeMessage(this, data));
      });
    }
  }

  const fakeWindow = new FakeWindow();
  installApiHarvester(fakeWindow as unknown as Window);

  const result = await new Promise<Record<string, unknown>>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("search api response timeout")), 500);
    fakeWindow.addEventListener("message", function onMessage(event) {
      const data = (event as MessageEvent).data as Record<string, unknown>;
      if (data?.type !== "OPENBILICLAW_DOUYIN_SEARCH_API_RESPONSE") return;
      if (data.requestId !== "search-req") return;
      clearTimeout(timer);
      fakeWindow.removeEventListener("message", onMessage);
      resolve(data);
    });
    fakeWindow.dispatchEvent(
      bridgeMessage(fakeWindow, {
          type: "OPENBILICLAW_DOUYIN_SEARCH_API_REQUEST",
          requestId: "search-req",
          keyword: "猫",
          maxItems: 5,
      }),
    );
  });

  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0]!.credentials, "include");
  assert.equal(fetchCalls[0]!.url.includes("X-Bogus=signed-xbogus"), true);
  assert.equal(fetchCalls[0]!.url.includes("search_channel=aweme_video_web"), true);
  assert.equal((result.items as { aweme_id: string }[])[0]!.aweme_id, "signed-search-1");
});

test("search API preserves its first page and reports a later rate limit", async () => {
  let requests = 0;
  class FakeWindow extends EventTarget {
    location = { origin: "https://www.douyin.com" };
    async fetch() {
      requests += 1;
      return requests === 1
        ? new Response(JSON.stringify({ status_code: 0, has_more: true, cursor: 20,
          data: [{ aweme_info: { aweme_id: "before-limit", desc: "已取得" } }] }))
        : new Response("", { status: 429 });
    }
    postMessage(data: unknown) { queueMicrotask(() => this.dispatchEvent(bridgeMessage(this, data))); }
  }
  const target = new FakeWindow();
  installApiHarvester(target as unknown as Window);
  const result = await new Promise<Record<string, unknown>>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("search reply missing")), 2000);
    target.addEventListener("message", event => {
      const data = (event as MessageEvent).data as Record<string, unknown>;
      if (data.type !== "OPENBILICLAW_DOUYIN_SEARCH_API_RESPONSE") return;
      clearTimeout(timer);
      resolve(data);
    });
    target.dispatchEvent(bridgeMessage(target, {
      type: "OPENBILICLAW_DOUYIN_SEARCH_API_REQUEST", requestId: "late-limit", keyword: "热点", maxItems: 12,
    }));
  });
  assert.equal(requests, 2);
  assert.equal(result.error, "HTTP 429");
  assert.equal((result.items as { aweme_id: string }[])[0].aweme_id, "before-limit");
});

for (const [name, response, expectedError] of [
  ["affirmative empty", { status_code: 0, data: [], has_more: false }, undefined],
  ["soft block", { status_code: 0, data: [], search_nil_info: { search_nil_item: "hit_shark" } }, "hit_shark"],
  ["rejected session", { status_code: 8, data: [] }, "api_status_8"],
] as const) {
  test(`search API ${name} does not probe a second endpoint`, async () => {
    let requests = 0;
    class FakeWindow extends EventTarget {
      location = { origin: "https://www.douyin.com" };
      async fetch() { requests += 1; return new Response(JSON.stringify(response)); }
      postMessage(data: unknown) { queueMicrotask(() => this.dispatchEvent(bridgeMessage(this, data))); }
    }
    const target = new FakeWindow();
    installApiHarvester(target as unknown as Window);
    const result = await new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("search reply missing")), 500);
      target.addEventListener("message", event => {
        const data = (event as MessageEvent).data as Record<string, unknown>;
        if (data.type !== "OPENBILICLAW_DOUYIN_SEARCH_API_RESPONSE") return;
        clearTimeout(timer);
        resolve(data);
      });
      target.dispatchEvent(bridgeMessage(target, {
        type: "OPENBILICLAW_DOUYIN_SEARCH_API_REQUEST", requestId: "one-request",
        keyword: "热点", maxItems: 12,
      }));
    });
    assert.equal(requests, 1);
    assert.equal(result.error, expectedError);
    assert.deepEqual(result.items, []);
  });
}

test("installApiHarvester signs hot related URLs and returns dy_hot items", async () => {
  const fetchCalls: { url: string; credentials?: RequestCredentials }[] = [];
  const fakeFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    fetchCalls.push({ url, credentials: init?.credentials });
    return new Response(
      JSON.stringify({
        status_code: 0,
        aweme_list: [{ aweme_id: "related-signed-1", desc: "热点 related" }],
      }),
    );
  };

  class FakeWindow extends EventTarget {
    fetch = fakeFetch;
    location = { origin: "https://www.douyin.com" };
    byted_acrawler = {
      frontierSign({ url }: { url: string }) {
        assert.equal(url.includes("/aweme/v1/web/aweme/related/"), true);
        assert.equal(url.includes("aweme_id=seed-1"), true);
        return { "X-Bogus": "related-xbogus" };
      },
    };

    postMessage(data: unknown): void {
      queueMicrotask(() => {
        this.dispatchEvent(bridgeMessage(this, data));
      });
    }
  }

  const fakeWindow = new FakeWindow();
  installApiHarvester(fakeWindow as unknown as Window);

  const result = await new Promise<Record<string, unknown>>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("hot related api response timeout")), 500);
    fakeWindow.addEventListener("message", function onMessage(event) {
      const data = (event as MessageEvent).data as Record<string, unknown>;
      if (data?.type !== "OPENBILICLAW_DOUYIN_HOT_API_RESPONSE") return;
      if (data.requestId !== "hot-req") return;
      clearTimeout(timer);
      fakeWindow.removeEventListener("message", onMessage);
      resolve(data);
    });
    fakeWindow.dispatchEvent(
      bridgeMessage(fakeWindow, {
          type: "OPENBILICLAW_DOUYIN_HOT_API_REQUEST",
          requestId: "hot-req",
          seedAwemeId: "seed-1",
          maxItems: 5,
          word: "热点词",
          sentenceId: "2495363",
      }),
    );
  });

  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0]!.credentials, "include");
  assert.equal(fetchCalls[0]!.url.includes("X-Bogus=related-xbogus"), true);
  assert.equal(fetchCalls[0]!.url.includes("/aweme/v1/web/aweme/related/"), true);
  const items = result.items as { scope: string; aweme_id: string; hot_word: string }[];
  assert.equal(items[0]!.scope, "dy_hot");
  assert.equal(items[0]!.aweme_id, "related-signed-1");
  assert.equal(items[0]!.hot_word, "热点词");
});

test("installApiHarvester signs tab feed URLs and returns dy_feed items", async () => {
  const fetchCalls: { url: string; credentials?: RequestCredentials }[] = [];
  const fakeFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    fetchCalls.push({ url, credentials: init?.credentials });
    return new Response(
      JSON.stringify({
        status_code: 0,
        aweme_list: [{ aweme_id: "feed-signed-1", desc: "首页推荐 signed" }],
      }),
    );
  };

  class FakeWindow extends EventTarget {
    fetch = fakeFetch;
    location = { origin: "https://www.douyin.com" };
    byted_acrawler = {
      frontierSign({ url }: { url: string }) {
        assert.equal(url.includes("/aweme/v1/web/tab/feed/"), true);
        assert.equal(url.includes("refresh_index=1"), true);
        assert.equal(url.includes("count=10"), true);
        assert.equal(url.includes("aweme_pc_rec_raw_data="), true);
        return { "X-Bogus": "feed-xbogus" };
      },
    };

    postMessage(data: unknown): void {
      queueMicrotask(() => {
        this.dispatchEvent(bridgeMessage(this, data));
      });
    }
  }

  const fakeWindow = new FakeWindow();
  installApiHarvester(fakeWindow as unknown as Window);

  const result = await new Promise<Record<string, unknown>>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("feed api response timeout")), 500);
    fakeWindow.addEventListener("message", function onMessage(event) {
      const data = (event as MessageEvent).data as Record<string, unknown>;
      if (data?.type !== "OPENBILICLAW_DOUYIN_FEED_API_RESPONSE") return;
      if (data.requestId !== "feed-req") return;
      clearTimeout(timer);
      fakeWindow.removeEventListener("message", onMessage);
      resolve(data);
    });
    fakeWindow.dispatchEvent(
      bridgeMessage(fakeWindow, {
          type: "OPENBILICLAW_DOUYIN_FEED_API_REQUEST",
          requestId: "feed-req",
          maxItems: 5,
      }),
    );
  });

  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0]!.credentials, "include");
  assert.equal(fetchCalls[0]!.url.includes("X-Bogus=feed-xbogus"), true);
  assert.equal(fetchCalls[0]!.url.includes("/aweme/v1/web/tab/feed/"), true);
  const items = result.items as { scope: string; aweme_id: string }[];
  assert.equal(items[0]!.scope, "dy_feed");
  assert.equal(items[0]!.aweme_id, "feed-signed-1");
});
