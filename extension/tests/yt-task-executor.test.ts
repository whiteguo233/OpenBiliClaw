/**
 * Tests for the YouTube content-script DOM extractors (issue #173).
 *
 * YouTube migrated history/search cards from Polymer (``ytd-video-renderer``)
 * to Lit (``yt-video-card-renderer`` / ``yt-lockup-view-model``); the old
 * selectors matched nothing, so bootstrap_profile returned 0 items. These
 * tests pin the new selector coverage + fallback extraction.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  extractChannelId,
  extractChannelItems,
  extractShortsId,
  extractVideoId,
  extractVideoItems,
  queryIncludingShadow,
} from "../src/content/yt/task-executor.ts";

type YtScope = "yt_history" | "yt_subscriptions" | "yt_likes";

interface FakeElement {
  textContent?: string;
  href?: string;
  src?: string;
  title?: string;
  ariaLabel?: string;
  children: Record<string, FakeElement | null>;
  shadowRoot?: { children: Record<string, FakeElement | null> };
}

function makeEl(
  fields: Partial<Omit<FakeElement, "children" | "shadowRoot">> = {},
  children: Record<string, FakeElement | null> = {},
  shadowRoot?: { children: Record<string, FakeElement | null> },
): FakeElement {
  return { textContent: "", ...fields, children, shadowRoot };
}

function collectDescendants(el: FakeElement): FakeElement[] {
  const out: FakeElement[] = [];
  for (const child of Object.values(el.children)) {
    if (child) {
      out.push(child);
      out.push(...collectDescendants(child));
    }
  }
  return out;
}

function asNode(el: FakeElement): any {
  return {
    textContent: el.textContent ?? "",
    href: el.href ?? "",
    src: el.src ?? "",
    title: el.title ?? "",
    getAttribute: (name: string) => {
      if (name === "aria-label") return el.ariaLabel ?? null;
      if (name === "href") return el.href ?? null;
      return null;
    },
    querySelector: (sel: string) => {
      const child = el.children[sel];
      return child === undefined ? null : asNode(child);
    },
    querySelectorAll: (sel: string) =>
      sel === "*" ? collectDescendants(el).map((c) => asNode(c)) : [],
    shadowRoot: el.shadowRoot
      ? {
          querySelector: (sel: string) => {
            const children = el.shadowRoot!.children;
            const child = children[sel];
            return child === undefined ? null : asNode(child);
          },
          querySelectorAll: () => [],
        }
      : null,
  };
}

function withDocument(renderers: FakeElement[]): () => void {
  const saved = (globalThis as Record<string, unknown>).document;
  (globalThis as Record<string, unknown>).document = {
    querySelectorAll: () => renderers.map((r) => asNode(r)),
  };
  return () => {
    (globalThis as Record<string, unknown>).document = saved;
  };
}

function videoCard(overrides: {
  title?: string;
  href?: string;
  channel?: string;
  cover?: string;
} = {}): FakeElement {
  const { title = "测试视频标题", href = "https://www.youtube.com/watch?v=AbCdEfGhIjk", channel = "某频道", cover = "https://i.ytimg.com/vi/AbCdEfGhIjk/hqdefault.jpg" } = overrides;
  const thumb: FakeElement = { textContent: "", children: {}, href: "", src: cover, title: "", ariaLabel: "" };
  return makeEl(
    {},
    {
      "a#thumbnail, a#video-title-link, a[id='thumbnail']": makeEl({ href }),
      "#video-title, #video-title-link": makeEl({ textContent: title }),
      "#channel-name a, ytd-channel-name a, .ytd-channel-name a": makeEl({ textContent: channel }),
      "img#img, img.yt-thumbnail-view-model-wiz__image, yt-image img": thumb,
    },
  );
}

// ---------------------------------------------------------------------------
// extractVideoId / extractShortsId
// ---------------------------------------------------------------------------

test("extractVideoId parses watch v= parameter", () => {
  assert.equal(extractVideoId("https://www.youtube.com/watch?v=AbCdEfGhIjk"), "AbCdEfGhIjk");
  assert.equal(extractVideoId("https://www.youtube.com/watch"), "");
});

test("extractShortsId parses /shorts/ path", () => {
  assert.equal(extractShortsId("https://www.youtube.com/shorts/0ERHMNIATpo"), "0ERHMNIATpo");
  assert.equal(extractShortsId("https://www.youtube.com/watch?v=AbCdEfGhIjk"), "");
});

// ---------------------------------------------------------------------------
// extractVideoItems — legacy Polymer selectors
// ---------------------------------------------------------------------------

test("extractVideoItems extracts from ytd-video-renderer cards", () => {
  const restore = withDocument([
    makeEl({}, {
      "a#thumbnail, a#video-title-link, a[id='thumbnail']": makeEl({ href: "/watch?v=AbCdEfGhIjk" }),
      "#video-title, #video-title-link": makeEl({ textContent: "旧版视频标题" }),
      "#channel-name a, ytd-channel-name a, .ytd-channel-name a": makeEl({ textContent: "旧频道" }),
      "img#img, img.yt-thumbnail-view-model-wiz__image, yt-image img": makeEl({ src: "https://i.ytimg.com/vi/AbCdEfGhIjk/1.jpg" }),
    }),
  ]);
  try {
    const items = extractVideoItems("yt_history" as YtScope);
    assert.equal(items.length, 1);
    assert.equal(items[0].video_id, "AbCdEfGhIjk");
    assert.equal(items[0].title, "旧版视频标题");
    assert.equal(items[0].channel, "旧频道");
  } finally {
    restore();
  }
});

// ---------------------------------------------------------------------------
// extractVideoItems — new Lit selectors (issue #173)
// ---------------------------------------------------------------------------

test("extractVideoItems extracts from yt-video-card-renderer cards", () => {
  const restore = withDocument([videoCard({ href: "/watch?v=XyZ12345678", title: "新版卡片标题" })]);
  try {
    const items = extractVideoItems("yt_history" as YtScope);
    assert.equal(items.length, 1);
    assert.equal(items[0].video_id, "XyZ12345678");
    assert.equal(items[0].title, "新版卡片标题");
    assert.equal(items[0].channel, "某频道");
    assert.equal(items[0].cover_url, "https://i.ytimg.com/vi/AbCdEfGhIjk/hqdefault.jpg");
  } finally {
    restore();
  }
});

test("extractVideoItems extracts title/channel/cover from current yt-lockup-view-model light DOM classes", () => {
  const restore = withDocument([
    makeEl({}, {
      'a[href*="/watch"], a[href*="/shorts/"]': makeEl({ href: "/watch?v=CurLockup12" }),
      "a.ytLockupMetadataViewModelTitle": makeEl({ textContent: "当前 Lockup 标题" }),
      "yt-content-metadata-view-model span.ytAttributedStringHost": makeEl({ textContent: "当前频道" }),
      "yt-thumbnail-view-model img, img.ytCoreImageHost": makeEl({ src: "https://i.ytimg.com/vi/CurLockup12/hqdefault.jpg" }),
    }),
  ]);
  try {
    const items = extractVideoItems("yt_history" as YtScope);
    assert.equal(items.length, 1);
    assert.equal(items[0].video_id, "CurLockup12");
    assert.equal(items[0].title, "当前 Lockup 标题");
    assert.equal(items[0].channel, "当前频道");
    assert.equal(items[0].cover_url, "https://i.ytimg.com/vi/CurLockup12/hqdefault.jpg");
  } finally {
    restore();
  }
});

test("queryIncludingShadow recurses into an open shadow root", () => {
  const card: FakeElement = makeEl({}, {}, {
    children: {
      "#video-title, #video-title-link": makeEl({ textContent: "Shadow 里的标题" }),
    },
  });
  const restore = withDocument([card]);
  try {
    const found = queryIncludingShadow(asNode(card), "#video-title, #video-title-link");
    assert.ok(found, "should find the title inside the open shadow root");
    assert.equal(found.textContent, "Shadow 里的标题");
  } finally {
    restore();
  }
});

test("extractVideoItems extracts a card whose content lives in an open shadow root", () => {
  const restore = withDocument([
    makeEl({}, {}, {
      children: {
        "a#thumbnail, a#video-title-link, a[id='thumbnail']": makeEl({ href: "/watch?v=Shadow12345" }),
        "#video-title, #video-title-link": makeEl({ textContent: "Shadow 卡片标题" }),
        "#channel-name a, ytd-channel-name a, .ytd-channel-name a": makeEl({ textContent: "Shadow 频道" }),
        "img#img, img.yt-thumbnail-view-model-wiz__image, yt-image img": makeEl({ src: "https://i.ytimg.com/vi/Shadow12345/1.jpg" }),
      },
    }),
  ]);
  try {
    const items = extractVideoItems("yt_history" as YtScope);
    assert.equal(items.length, 1);
    assert.equal(items[0].video_id, "Shadow12345");
    assert.equal(items[0].title, "Shadow 卡片标题");
    assert.equal(items[0].channel, "Shadow 频道");
  } finally {
    restore();
  }
});

test("extractVideoItems falls back to any /watch anchor when title element is missing", () => {
  const card: FakeElement = makeEl({}, {
    // 只有通用 /watch 链接，没有 #thumbnail / #video-title
    'a[href*="/watch"], a[href*="/shorts/"]': makeEl({ href: "https://www.youtube.com/watch?v=AbCdEfGhIjk", ariaLabel: "回退标题 via aria-label" }),
    "#channel-name": makeEl({ textContent: "兜底频道" }),
  });
  const restore = withDocument([card]);
  try {
    const items = extractVideoItems("yt_likes" as YtScope);
    assert.equal(items.length, 1);
    assert.equal(items[0].video_id, "AbCdEfGhIjk");
    assert.equal(items[0].title, "回退标题 via aria-label");
  } finally {
    restore();
  }
});

test("extractVideoItems extracts Shorts from ytd-reel-item-renderer", () => {
  const restore = withDocument([
    makeEl({}, {
      "a#thumbnail, a#video-title-link, a[id='thumbnail']": makeEl({ href: "/shorts/0ERHMNIATpo" }),
      "#video-title, #video-title-link": makeEl({ textContent: "短视频标题" }),
      "#channel-name a, ytd-channel-name a, .ytd-channel-name a": makeEl({ textContent: "短片频道" }),
    }),
  ]);
  try {
    const items = extractVideoItems("yt_history" as YtScope);
    assert.equal(items.length, 1);
    assert.equal(items[0].video_id, "0ERHMNIATpo");
    assert.match(items[0].url ?? "", /\/shorts\//);
  } finally {
    restore();
  }
});

test("extractVideoItems deduplicates by video id", () => {
  const restore = withDocument([videoCard(), videoCard({ href: "/watch?v=AbCdEfGhIjk", title: "重复" })]);
  try {
    const items = extractVideoItems("yt_history" as YtScope);
    assert.equal(items.length, 1);
  } finally {
    restore();
  }
});

test("extractVideoItems skips empty cards without title or id", () => {
  const restore = withDocument([
    makeEl({}, { "a#thumbnail, a#video-title-link, a[id='thumbnail']": makeEl({ href: "" }) }),
  ]);
  try {
    const items = extractVideoItems("yt_history" as YtScope);
    assert.equal(items.length, 0);
  } finally {
    restore();
  }
});

// ---------------------------------------------------------------------------
// extractChannelItems
// ---------------------------------------------------------------------------

test("extractChannelItems extracts from ytd-channel-renderer", () => {
  const restore = withDocument([
    makeEl({}, {
      "#channel-title, #channel-name, #name": makeEl({ textContent: "订阅频道" }),
      "a#main-link, a#channel-title-link, a.channel-link": makeEl({ href: "/channel/UCAbCdEfGhIjK1234567890" }),
      "img#img, yt-img-shadow img, yt-image img": makeEl({ src: "https://yt3.googleusercontent.com/x.jpg" }),
    }),
  ]);
  try {
    const items = extractChannelItems("yt_subscriptions" as YtScope);
    assert.equal(items.length, 1);
    assert.equal(items[0].channel_id, "UCAbCdEfGhIjK1234567890");
    assert.equal(items[0].title, "订阅频道");
  } finally {
    restore();
  }
});

test("extractChannelItems falls back to /@ handle anchor (new card layouts)", () => {
  const restore = withDocument([
    makeEl({}, {
      "#channel-title, #channel-name, #name": makeEl({ textContent: "新频道" }),
      'a[href*="/channel/"], a[href*="/@"]': makeEl({ href: "https://www.youtube.com/@newchannel" }),
    }),
  ]);
  try {
    const items = extractChannelItems("yt_subscriptions" as YtScope);
    assert.equal(items.length, 1);
    assert.equal(items[0].title, "新频道");
    assert.equal(items[0].url, "https://www.youtube.com/@newchannel");
  } finally {
    restore();
  }
});

test("extractChannelId parses channel id", () => {
  assert.equal(extractChannelId("https://www.youtube.com/channel/UCAbCdEfGhIjK1234567890"), "UCAbCdEfGhIjK1234567890");
  assert.equal(extractChannelId("https://www.youtube.com/@handle"), "");
});
