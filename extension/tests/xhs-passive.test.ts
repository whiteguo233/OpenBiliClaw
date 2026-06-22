/**
 * Tests for the xhs passive URL collector.
 *
 * The collector never scrolls — it only extracts URLs that the user's own
 * browsing has already rendered into (or adjacent to) the viewport. The
 * tests exercise pure helpers that operate on minimal "anchor-like"
 * objects so we can run under node --test without jsdom.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  classifyXhsPageType,
  extractXhsNoteUrl,
  extractNoteMetadataFromAnchor,
  collectInViewportNoteUrls,
  dedupeObservedUrls,
  filterSelfAuthoredNotes,
  type AnchorLike,
  type ViewportRect,
  type XhsNoteMetadata,
  type XhsSelfInfo,
} from "../src/content/xhs/passive.ts";

const VIEWPORT: ViewportRect = { top: 0, bottom: 800, height: 800 };

function anchor(
  href: string,
  rect: Partial<DOMRect> & { top: number; bottom: number },
): AnchorLike {
  return {
    href,
    rect: {
      top: rect.top,
      bottom: rect.bottom,
      left: 0,
      right: 1200,
      width: 1200,
      height: rect.bottom - rect.top,
      x: 0,
      y: rect.top,
    } as DOMRect,
  };
}

class FakeDomElement {
  readonly textContent: string;
  readonly title: string;
  readonly href: string;
  private readonly attrs: Record<string, string>;
  private readonly selectorMap: Record<string, FakeDomElement[]>;
  private readonly closestElement?: FakeDomElement;

  constructor(opts: {
    textContent?: string;
    title?: string;
    href?: string;
    attrs?: Record<string, string>;
    selectorMap?: Record<string, FakeDomElement[]>;
    closestElement?: FakeDomElement;
  } = {}) {
    this.textContent = opts.textContent ?? "";
    this.title = opts.title ?? "";
    this.href = opts.href ?? "";
    this.attrs = opts.attrs ?? {};
    this.selectorMap = opts.selectorMap ?? {};
    this.closestElement = opts.closestElement;
  }

  closest(): FakeDomElement {
    return this.closestElement ?? this;
  }

  querySelector(selector: string): FakeDomElement | null {
    return this.querySelectorAll(selector)[0] ?? null;
  }

  querySelectorAll(selector: string): FakeDomElement[] {
    if (selector.includes("[aria-label]")) {
      return this.selectorMap.metrics ?? [];
    }
    return this.selectorMap[selector] ?? [];
  }

  getAttribute(name: string): string | null {
    if (name === "title") return this.title || this.attrs[name] || null;
    if (name === "href") return this.href || this.attrs[name] || null;
    return this.attrs[name] ?? null;
  }
}

test("classifyXhsPageType identifies search / explore / profile / other", () => {
  assert.equal(
    classifyXhsPageType("https://www.xiaohongshu.com/search_result?keyword=x"),
    "search",
  );
  assert.equal(
    classifyXhsPageType("https://www.xiaohongshu.com/user/profile/abc"),
    "profile",
  );
  assert.equal(
    classifyXhsPageType("https://www.xiaohongshu.com/explore/abc123"),
    "note",
  );
  assert.equal(
    classifyXhsPageType("https://www.xiaohongshu.com/explore"),
    "explore",
  );
  assert.equal(
    classifyXhsPageType("https://www.xiaohongshu.com/messages"),
    "other",
  );
});

test("extractXhsNoteUrl normalises relative hrefs and keeps xsec_token", () => {
  const absolute = extractXhsNoteUrl(
    "/explore/abc123def456?xsec_token=ZZZ&source=homefeed",
    "https://www.xiaohongshu.com/search_result?keyword=x",
  );
  assert.equal(
    absolute,
    "https://www.xiaohongshu.com/explore/abc123def456?xsec_token=ZZZ",
  );
});

test("extractXhsNoteUrl rejects non-note URLs", () => {
  assert.equal(
    extractXhsNoteUrl(
      "/user/profile/abc",
      "https://www.xiaohongshu.com/explore",
    ),
    null,
  );
  assert.equal(
    extractXhsNoteUrl("javascript:void(0)", "https://www.xiaohongshu.com/"),
    null,
  );
});

test("extractXhsNoteUrl keeps discovery/item variant", () => {
  const url = extractXhsNoteUrl(
    "https://www.xiaohongshu.com/discovery/item/abc123?xsec_token=YY",
    "https://www.xiaohongshu.com/user/profile/me",
  );
  assert.equal(
    url,
    "https://www.xiaohongshu.com/discovery/item/abc123?xsec_token=YY",
  );
});

test("collectInViewportNoteUrls filters anchors overlapping the viewport", () => {
  const anchors: AnchorLike[] = [
    anchor("/explore/aaa?xsec_token=1", { top: 100, bottom: 300 }), // in view
    anchor("/explore/bbb?xsec_token=2", { top: 900, bottom: 1100 }), // below
    anchor("/user/profile/c", { top: 50, bottom: 120 }), // in view but not a note
    anchor("/explore/ddd?xsec_token=4", { top: -200, bottom: -50 }), // above
    anchor("/discovery/item/eee?xsec_token=5", { top: 500, bottom: 700 }), // in view
  ];

  const urls = collectInViewportNoteUrls(anchors, VIEWPORT, {
    baseUrl: "https://www.xiaohongshu.com/search_result?keyword=x",
  });

  assert.deepEqual(urls, [
    "https://www.xiaohongshu.com/explore/aaa?xsec_token=1",
    "https://www.xiaohongshu.com/discovery/item/eee?xsec_token=5",
  ]);
});

test("collectInViewportNoteUrls allows a near-viewport tolerance band", () => {
  const anchors: AnchorLike[] = [
    anchor("/explore/near?xsec_token=1", { top: 820, bottom: 950 }), // just below
  ];

  const urls = collectInViewportNoteUrls(anchors, VIEWPORT, {
    baseUrl: "https://www.xiaohongshu.com/explore",
    toleranceBelowPx: 200,
  });

  assert.deepEqual(urls, [
    "https://www.xiaohongshu.com/explore/near?xsec_token=1",
  ]);
});

test("collectInViewportNoteUrls deduplicates repeated cards", () => {
  const anchors: AnchorLike[] = [
    anchor("/explore/aaa?xsec_token=1", { top: 100, bottom: 200 }),
    anchor("/explore/aaa?xsec_token=1", { top: 300, bottom: 400 }),
  ];

  const urls = collectInViewportNoteUrls(anchors, VIEWPORT, {
    baseUrl: "https://www.xiaohongshu.com/",
  });

  assert.equal(urls.length, 1);
});

test("extractNoteMetadataFromAnchor reads visible metric chips", () => {
  const titleEl = new FakeDomElement({ textContent: "手冲咖啡入门" });
  const authorEl = new FakeDomElement({ textContent: "豆子老师" });
  const cover = new FakeDomElement({
    attrs: { src: "https://sns-webpic-qc.xhscdn.com/cover.jpg" },
  });
  const card = new FakeDomElement({
    selectorMap: {
      ".title, .note-title, [class*='title'] span, [class*='title']": [titleEl],
      ".author-wrapper .name, .author .name, .user-name, [class*='author'] .name, .nickname": [
        authorEl,
      ],
      "img.cover, .cover img, img[src*='xhscdn'], img[src*='sns-img'], img": [cover],
      metrics: [
        new FakeDomElement({ textContent: "浏览 1.2万" }),
        new FakeDomElement({ textContent: "赞 42" }),
        new FakeDomElement({ textContent: "收藏 1,234" }),
        new FakeDomElement({ textContent: "评论 3k" }),
      ],
    },
  });
  const anchorEl = new FakeDomElement({
    href: "/explore/note-1?xsec_token=tok",
    closestElement: card,
  });

  const meta = extractNoteMetadataFromAnchor(
    anchorEl as unknown as HTMLAnchorElement,
    "https://www.xiaohongshu.com/search_result?keyword=x",
  );

  assert.deepEqual(meta, {
    url: "https://www.xiaohongshu.com/explore/note-1?xsec_token=tok",
    title: "手冲咖啡入门",
    author: "豆子老师",
    cover_url: "https://sns-webpic-qc.xhscdn.com/cover.jpg",
    view_count: 12_000,
    like_count: 42,
    collect_count: 1_234,
    comment_count: 3_000,
  });
});

test("dedupeObservedUrls removes previously reported URLs", () => {
  const seen = new Set<string>(["https://www.xiaohongshu.com/explore/aaa?xsec_token=1"]);
  const fresh = dedupeObservedUrls(
    [
      "https://www.xiaohongshu.com/explore/aaa?xsec_token=1",
      "https://www.xiaohongshu.com/explore/bbb?xsec_token=2",
    ],
    seen,
  );
  assert.deepEqual(fresh, ["https://www.xiaohongshu.com/explore/bbb?xsec_token=2"]);
  assert.equal(seen.size, 2);
});

// v0.3.10+: scrape-time self-author filter — even before the backend
// gate fires, the extension drops notes whose author matches the
// logged-in user. Defends against XHS search/explore feeds that mix
// the user's own posts back into the result set.

test("filterSelfAuthoredNotes drops notes whose author matches self.nickname", () => {
  const notes: XhsNoteMetadata[] = [
    { url: "u1", title: "self post", author: "屎屎", cover_url: "" },
    { url: "u2", title: "stranger post", author: "Jupiter", cover_url: "" },
  ];
  const self: XhsSelfInfo = { user_id: "uid-1", nickname: "屎屎" };
  const filtered = filterSelfAuthoredNotes(notes, self);
  assert.equal(filtered.length, 1);
  assert.equal(filtered[0].author, "Jupiter");
});

test("filterSelfAuthoredNotes is case-insensitive", () => {
  const notes: XhsNoteMetadata[] = [
    { url: "u", title: "x", author: "  Shi Shi  ", cover_url: "" },
  ];
  const self: XhsSelfInfo = { user_id: "", nickname: "shi shi" };
  assert.deepEqual(filterSelfAuthoredNotes(notes, self), []);
});

test("filterSelfAuthoredNotes is a no-op when self_info is null", () => {
  const notes: XhsNoteMetadata[] = [
    { url: "u", title: "x", author: "anyone", cover_url: "" },
  ];
  assert.equal(filterSelfAuthoredNotes(notes, null).length, 1);
});

test("filterSelfAuthoredNotes is a no-op when self.nickname is empty", () => {
  const notes: XhsNoteMetadata[] = [
    { url: "u", title: "x", author: "anyone", cover_url: "" },
  ];
  const self: XhsSelfInfo = { user_id: "uid", nickname: "" };
  assert.equal(filterSelfAuthoredNotes(notes, self).length, 1);
});
