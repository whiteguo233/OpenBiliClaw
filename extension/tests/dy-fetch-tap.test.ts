/**
 * Tests for the Douyin MAIN-world fetch-tap.
 *
 * Task 3 of the Douyin bootstrap import plan
 * (docs/plans/2026-05-06-douyin-bootstrap-import.md). The module
 * itself does NOT auto-install on import — installFetchTap is called
 * explicitly by the content-script, so importing here under node:test
 * (no window) does not trigger side effects.
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
  classifyDouyinResponseUrl,
  installFetchTap,
  installXhrTap,
  installApiHarvester,
  parseFeedAwemeResponse,
  parseRelatedAwemeResponse,
  parseSearchAwemeResponse,
  parseAwemeListResponse,
  parseUserFollowListResponse,
  waitForDouyinSdk,
} from "../src/main/dy-fetch-tap.ts";

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
});

test("parseSearchAwemeResponse accepts aweme_list from video search endpoint", () => {
  const items = parseSearchAwemeResponse({
    aweme_list: [{ aweme_id: "search-2", preview_title: "视频搜索 2" }],
  });
  assert.equal(items.length, 1);
  assert.equal(items[0]!.scope, "dy_search");
  assert.equal(items[0]!.aweme_id, "search-2");
  assert.equal(items[0]!.title, "视频搜索 2");
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

test("installFetchTap posts chunked search stream responses through optional search callback", async () => {
  const calls: { items: unknown[] }[] = [];
  const fakeFetch = async (): Promise<Response> =>
    new Response(
      '14c0\r\n{"status_code":0,"data":[{"aweme_info":{"aweme_id":"stream-search-1","desc":"搜索 stream"}}]}',
      { status: 200, headers: { "content-type": "application/json" } },
    );
  const fakeWindow = { fetch: fakeFetch } as unknown as Window;
  installFetchTap(
    fakeWindow,
    () => {},
    (items) => calls.push({ items }),
  );
  await fakeWindow.fetch(
    "https://www.douyin.com/aweme/v1/web/general/search/stream/?keyword=%E7%A7%91%E6%8A%80",
  );
  assert.equal(calls.length, 1);
  assert.equal((calls[0]!.items[0] as { aweme_id: string }).aweme_id, "stream-search-1");
});

test("installFetchTap passively posts feed responses through optional search callback", async () => {
  const calls: { items: unknown[] }[] = [];
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
    (items) => calls.push({ items }),
  );
  await fakeWindow.fetch("https://www.douyin.com/aweme/v1/web/tab/feed/?count=10");
  await fakeWindow.fetch("https://www.douyin.com/aweme/v2/web/module/feed/?count=20");
  assert.equal(calls.length, 2);
  assert.equal((calls[0]!.items[0] as { scope: string }).scope, "dy_feed");
  assert.equal((calls[0]!.items[0] as { aweme_id: string }).aweme_id, "feed-passive-1");
  assert.equal((calls[1]!.items[0] as { scope: string }).scope, "dy_feed");
  assert.equal((calls[1]!.items[0] as { aweme_id: string }).aweme_id, "feed-passive-1");
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

test("installApiHarvester paginates favorite and like scopes through the postMessage bridge", async () => {
  const fetchCalls: { url: string; credentials?: RequestCredentials }[] = [];
  const favoritePages = new Map<number, unknown>([
    [
      0,
      {
        has_more: true,
        max_cursor: 123,
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
        this.dispatchEvent(new MessageEvent("message", { data }));
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
        new MessageEvent("message", {
          data: {
            type: "OPENBILICLAW_DOUYIN_API_REQUEST",
            requestId,
            scope,
            secUid: "MS4wTestUser",
            maxItems: 10,
          },
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
        this.dispatchEvent(new MessageEvent("message", { data }));
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
      new MessageEvent("message", {
        data: {
          type: "OPENBILICLAW_DOUYIN_SEARCH_API_REQUEST",
          requestId: "search-req",
          keyword: "猫",
          maxItems: 5,
        },
      }),
    );
  });

  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0]!.credentials, "include");
  assert.equal(fetchCalls[0]!.url.includes("X-Bogus=signed-xbogus"), true);
  assert.equal(fetchCalls[0]!.url.includes("search_channel=aweme_video_web"), true);
  assert.equal((result.items as { aweme_id: string }[])[0]!.aweme_id, "signed-search-1");
});

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
        this.dispatchEvent(new MessageEvent("message", { data }));
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
      new MessageEvent("message", {
        data: {
          type: "OPENBILICLAW_DOUYIN_HOT_API_REQUEST",
          requestId: "hot-req",
          seedAwemeId: "seed-1",
          maxItems: 5,
          word: "热点词",
          sentenceId: "2495363",
        },
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
        this.dispatchEvent(new MessageEvent("message", { data }));
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
      new MessageEvent("message", {
        data: {
          type: "OPENBILICLAW_DOUYIN_FEED_API_REQUEST",
          requestId: "feed-req",
          maxItems: 5,
        },
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
