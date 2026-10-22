/**
 * Tests for the TikTok DOM extractor's pure helpers — fixture-style fake
 * DOM, no real TikTok requests.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  detectTiktokAccessState,
  detectTiktokEmptyState,
  extractTiktokHandleFromHref,
  extractTiktokItemsFromDocument,
  extractTiktokVideoIdFromHref,
  isTiktokLoginWallUrl,
  pickTiktokScrollTarget,
  isTiktokOwnProfile,
  resolveTiktokScopeRoot,
} from "../src/content/tt/dom-extractor.ts";

class FakeElement {
  readonly textContent: string;
  readonly href: string;
  readonly className: string;
  readonly parentElement: FakeElement | null;
  readonly scrollHeight: number;
  readonly clientHeight: number;
  private readonly attrs: Record<string, string>;
  private readonly selectorMap: Record<string, FakeElement[]>;
  private readonly closestElement?: FakeElement;

  constructor(opts: {
    textContent?: string;
    href?: string;
    className?: string;
    attrs?: Record<string, string>;
    selectorMap?: Record<string, FakeElement[]>;
    closestElement?: FakeElement;
    parentElement?: FakeElement | null;
    scrollHeight?: number;
    clientHeight?: number;
  } = {}) {
    this.textContent = opts.textContent ?? "";
    this.href = opts.href ?? "";
    this.className = opts.className ?? "";
    this.attrs = opts.attrs ?? {};
    this.selectorMap = opts.selectorMap ?? {};
    this.closestElement = opts.closestElement;
    this.parentElement = opts.parentElement ?? null;
    this.scrollHeight = opts.scrollHeight ?? 0;
    this.clientHeight = opts.clientHeight ?? 0;
  }

  contains(other: FakeElement): boolean { return other === this; }

  closest(): FakeElement {
    return this.closestElement ?? this;
  }

  querySelector(selector: string): FakeElement | null {
    return this.querySelectorAll(selector)[0] ?? null;
  }

  querySelectorAll(selector: string): FakeElement[] {
    if (selector.includes("[aria-label]")) return this.selectorMap.metrics ?? [];
    if (selector === "span, p") return this.selectorMap.handleSpans ?? [];
    return this.selectorMap[selector] ?? [];
  }

  getAttribute(name: string): string | null {
    if (name === "href") return this.href || this.attrs[name] || null;
    if (name === "src") return this.attrs.src || null;
    if (name === "class") return this.className || this.attrs[name] || null;
    return this.attrs[name] ?? null;
  }
}

class FakeDocument {
  readonly body: { textContent: string; innerText: string };
  readonly defaultView: { getComputedStyle: (el: FakeElement) => { overflowY: string } } | null;
  private readonly videoAnchors: FakeElement[];
  private readonly profileAnchors: FakeElement[];
  private readonly textNodes: FakeElement[];
  private readonly presentSelectors: Set<string>;

  constructor(opts: {
    videoAnchors?: FakeElement[];
    profileAnchors?: FakeElement[];
    textNodes?: FakeElement[];
    presentSelectors?: string[];
    bodyText?: string;
    defaultView?: FakeDocument["defaultView"];
  } = {}) {
    this.videoAnchors = opts.videoAnchors ?? [];
    this.profileAnchors = opts.profileAnchors ?? [];
    this.textNodes = opts.textNodes ?? [];
    this.presentSelectors = new Set(opts.presentSelectors ?? []);
    this.body = { textContent: opts.bodyText ?? "", innerText: opts.bodyText ?? "" };
    this.defaultView = opts.defaultView ?? null;
  }

  querySelector(selector: string): FakeElement | null {
    if (this.presentSelectors.has(selector)) return new FakeElement();
    if (selector === 'a[href*="/video/"]') return this.videoAnchors[0] ?? null;
    if (selector === 'a[href*="/@"]') return this.profileAnchors[0] ?? null;
    return null;
  }

  querySelectorAll(selector: string): FakeElement[] {
    if (selector.includes('/video/') || selector.includes('/photo/')) return this.videoAnchors;
    if (selector === 'a[href*="/@"]') return this.profileAnchors;
    if (selector === "div, span, p, h1, h2, h3") return this.textNodes;
    return [];
  }
}

// ---------------------------------------------------------------------------
// href shape guards
// ---------------------------------------------------------------------------

test("extractTiktokVideoIdFromHref reads video and photo ids", () => {
  assert.equal(
    extractTiktokVideoIdFromHref("/@creator.one/video/7300000000000000001"),
    "7300000000000000001",
  );
  assert.equal(
    extractTiktokVideoIdFromHref(
      "https://www.tiktok.com/@creator.one/photo/7300000000000000002",
    ),
    "7300000000000000002",
  );
  assert.equal(extractTiktokVideoIdFromHref("/@creator.one"), "");
  assert.equal(extractTiktokVideoIdFromHref("/tag/cats"), "");
  assert.equal(extractTiktokVideoIdFromHref(""), "");
});

test("extractTiktokHandleFromHref validates handle shape", () => {
  assert.equal(extractTiktokHandleFromHref("/@creator.one"), "creator.one");
  assert.equal(
    extractTiktokHandleFromHref("https://www.tiktok.com/@creator_one/video/123456789"),
    "creator_one",
  );
  assert.equal(extractTiktokHandleFromHref("/user/MS4w123"), "");
  assert.equal(extractTiktokHandleFromHref("/@x"), "");
  assert.equal(extractTiktokHandleFromHref(""), "");
});

// ---------------------------------------------------------------------------
// Video-scope extraction
// ---------------------------------------------------------------------------

test("extractTiktokItemsFromDocument harvests normalized video rows", () => {
  const title = new FakeElement({ textContent: "cat compilation #fyp" });
  const image = new FakeElement({ attrs: { src: "https://p16.tiktokcdn.com/cover.jpg" } });
  const card = new FakeElement({
    selectorMap: {
      '[data-e2e="video-desc"]': [title],
      img: [image],
      metrics: [
        new FakeElement({ textContent: "1.2K likes" }),
        new FakeElement({ textContent: "34 comments" }),
        new FakeElement({ textContent: "56 shares" }),
        new FakeElement({ textContent: "1,234 favorites" }),
      ],
    },
  });
  const anchor = new FakeElement({
    href: "/@creator.one/video/7300000000000000001",
    closestElement: card,
  });
  const doc = new FakeDocument({ videoAnchors: [anchor] }) as unknown as Document;

  const items = extractTiktokItemsFromDocument(doc, "tt_like", "https://www.tiktok.com", 10);
  assert.deepEqual(items, [
    {
      scope: "tt_like",
      video_id: "7300000000000000001",
      url: "https://www.tiktok.com/@creator.one/video/7300000000000000001",
      title: "cat compilation #fyp",
      author_handle: "creator.one",
      author: "creator.one",
      cover_url: "https://p16.tiktokcdn.com/cover.jpg",
      like_count: 1_200,
      comment_count: 34,
      share_count: 56,
      collect_count: 1_234,
    },
  ]);
});

test("extractTiktokItemsFromDocument dedups repeat renders and respects the cap", () => {
  const a = new FakeElement({ href: "/@c/video/7300000000000000001" });
  const dup = new FakeElement({ href: "https://www.tiktok.com/@c/video/7300000000000000001" });
  const b = new FakeElement({ href: "/@c/video/7300000000000000002" });
  const c = new FakeElement({ href: "/@c/video/7300000000000000003" });
  const doc = new FakeDocument({ videoAnchors: [a, dup, b, c] }) as unknown as Document;

  const items = extractTiktokItemsFromDocument(doc, "tt_history", "https://www.tiktok.com", 2);
  assert.deepEqual(
    items.map((i) => i.video_id),
    ["7300000000000000001", "7300000000000000002"],
  );
  // Rows without a parseable video id are dropped, not fabricated.
  const junk = new FakeDocument({
    videoAnchors: [new FakeElement({ href: "/@c" })],
  }) as unknown as Document;
  assert.equal(extractTiktokItemsFromDocument(junk, "tt_like", "https://www.tiktok.com", 5).length, 0);
});

// ---------------------------------------------------------------------------
// Follow-scope extraction
// ---------------------------------------------------------------------------

test("extractTiktokItemsFromDocument harvests follow rows keyed by handle", () => {
  const nickname = new FakeElement({ textContent: "Creator Two" });
  const card = new FakeElement({ selectorMap: { '[class*="title"]': [nickname] } });
  const userA = new FakeElement({ href: "/@user.a", closestElement: card });
  const userB = new FakeElement({ href: "https://www.tiktok.com/@user.b" });
  const dup = new FakeElement({ href: "/@user.a" });
  // Author chip inside a video card — not a follow target.
  const videoChip = new FakeElement({ href: "/@user.a/video/7300000000000000001" });
  const doc = new FakeDocument({
    profileAnchors: [userA, userB, dup, videoChip],
  }) as unknown as Document;

  const items = extractTiktokItemsFromDocument(doc, "tt_follow", "https://www.tiktok.com", 10);
  assert.deepEqual(
    items.map((i) => i.author_handle),
    ["user.a", "user.b"],
  );
  assert.equal(items[0]!.video_id, "");
  assert.equal(items[0]!.title, "Creator Two");
  assert.equal(items[0]!.creator_sec_uid, "user.a");
  assert.equal(items[1]!.url, "https://www.tiktok.com/@user.b");
});

// ---------------------------------------------------------------------------
// Access-state detection — fail closed
// ---------------------------------------------------------------------------

test("detectTiktokAccessState recognizes signed-in markers", () => {
  const doc = new FakeDocument({ presentSelectors: ['[data-e2e="nav-profile"]'] });
  assert.equal(
    detectTiktokAccessState(doc as unknown as Document, "https://www.tiktok.com/"),
    "signed_in",
  );
});

test("detectTiktokAccessState flags login walls by URL, selector, and text", () => {
  const byUrl = new FakeDocument();
  assert.equal(
    detectTiktokAccessState(byUrl as unknown as Document, "https://www.tiktok.com/login"),
    "login_wall",
  );
  const bySelector = new FakeDocument({ presentSelectors: ['[data-e2e="top-login-button"]'] });
  assert.equal(
    detectTiktokAccessState(bySelector as unknown as Document, "https://www.tiktok.com/"),
    "login_wall",
  );
  const byText = new FakeDocument({ bodyText: "Log in to TikTok to continue" });
  assert.equal(
    detectTiktokAccessState(byText as unknown as Document, "https://www.tiktok.com/"),
    "login_wall",
  );
  // Visible login walls override stale signed-in chrome.
  const both = new FakeDocument({
    presentSelectors: ['[data-e2e="nav-profile"]', '[data-e2e="top-login-button"]'],
  });
  assert.equal(
    detectTiktokAccessState(both as unknown as Document, "https://www.tiktok.com/"),
    "login_wall",
  );
});

test("detectTiktokAccessState fails closed on unknown pages", () => {
  const doc = new FakeDocument({ bodyText: "Some totally unrelated page" });
  assert.equal(
    detectTiktokAccessState(doc as unknown as Document, "https://www.tiktok.com/@someone"),
    "logged_out",
  );
});

test("isTiktokLoginWallUrl only matches auth routes", () => {
  assert.equal(isTiktokLoginWallUrl("https://www.tiktok.com/login"), true);
  assert.equal(isTiktokLoginWallUrl("https://www.tiktok.com/signup/phone"), true);
  assert.equal(isTiktokLoginWallUrl("https://www.tiktok.com/@c/video/1"), false);
  // Junk input parses relative to the tiktok.com base and is not an auth
  // route; DOM detection still gets its say before failing closed.
  assert.equal(isTiktokLoginWallUrl("not a url /login"), false);
});

// ---------------------------------------------------------------------------
// Explicit empty state — the ONLY valid empty
// ---------------------------------------------------------------------------

test("detectTiktokEmptyState matches per-scope logged-in empty states", () => {
  const likedEmpty = new FakeDocument({
    textNodes: [new FakeElement({ textContent: "No liked videos yet" })],
  });
  assert.equal(
    detectTiktokEmptyState(likedEmpty as unknown as Document, "tt_like"),
    "no liked videos yet",
  );
  const historyEmpty = new FakeDocument({
    textNodes: [new FakeElement({ textContent: "暂无观看历史" })],
  });
  assert.equal(
    detectTiktokEmptyState(historyEmpty as unknown as Document, "tt_history"),
    "暂无观看历史",
  );
  // Scope mismatch: a like-empty phrase must not validate history emptiness.
  assert.equal(
    detectTiktokEmptyState(likedEmpty as unknown as Document, "tt_history"),
    "",
  );
  // Unrelated text never counts as an explicit empty.
  const unknown = new FakeDocument({
    textNodes: [new FakeElement({ textContent: "Something went wrong" })],
  });
  assert.equal(detectTiktokEmptyState(unknown as unknown as Document, "tt_collect"), "");
});

// ---------------------------------------------------------------------------
// Scroll target discovery
// ---------------------------------------------------------------------------

test("pickTiktokScrollTarget finds the nearest scrollable ancestor", () => {
  const scrollable = new FakeElement({ scrollHeight: 2_000, clientHeight: 800 });
  const wrapper = new FakeElement({ parentElement: scrollable });
  const anchor = new FakeElement({ parentElement: wrapper });
  const doc = new FakeDocument({
    videoAnchors: [anchor],
    defaultView: {
      getComputedStyle: (el: FakeElement) => ({
        overflowY: el === scrollable ? "auto" : "visible",
      }),
    },
  }) as unknown as Document;

  const root = Object.assign(doc, { ownerDocument: doc }) as unknown as HTMLElement;
  assert.equal(pickTiktokScrollTarget(root), scrollable as unknown as Element);
});

test("pickTiktokScrollTarget returns null without any list anchor", () => {
  const doc = new FakeDocument() as unknown as Document;
  assert.equal(pickTiktokScrollTarget(doc as unknown as HTMLElement), null);
});


test("own-profile proof rejects another account, video routes and unknown viewer", () => {
  assert.equal(isTiktokOwnProfile("https://www.tiktok.com/@me.user", "me.user"), true);
  assert.equal(isTiktokOwnProfile("https://www.tiktok.com/@other.user", "me.user"), false);
  assert.equal(isTiktokOwnProfile("https://www.tiktok.com/@me.user/video/123456", "me.user"), false);
  assert.equal(isTiktokOwnProfile("https://www.tiktok.com/@me.user", ""), false);
});

test("scope roots exclude the navbar and require an unambiguous active scope", () => {
  const ownLink = new FakeElement({ href: "/@me.user" });
  const followed = new FakeElement({ href: "/@friend.user" });
  const list = new FakeElement({ selectorMap: { 'a[href*="/@"]': [followed] } });
  const doc = {
    querySelectorAll(selector: string) {
      if (selector.includes('data-e2e="following-list"')) return [list];
      if (selector === 'a[href*="/@"]') return [ownLink, followed];
      return [];
    },
  } as unknown as Document;
  const root = resolveTiktokScopeRoot(doc, "tt_follow");
  assert.equal(root, list);
  assert.deepEqual(extractTiktokItemsFromDocument(root!, "tt_follow", "https://www.tiktok.com", 30)
    .map((row) => row.author_handle), ["friend.user"]);
  assert.equal(resolveTiktokScopeRoot(doc, "tt_like"), null);
  const ambiguous = { querySelectorAll: () => [list, new FakeElement()] } as unknown as Document;
  assert.equal(resolveTiktokScopeRoot(ambiguous, "tt_follow"), null);
});

test("inactive tabpanels cannot be mistaken for the requested scope", () => {
  const panel = new FakeElement({ attrs: { role: "tabpanel" } });
  const inactive = new FakeElement({ attrs: { "aria-selected": "false", "aria-controls": "liked" } });
  const active = new FakeElement({ attrs: { "aria-selected": "true", "aria-controls": "liked" } });
  const make = (tab: FakeElement) => ({
    querySelectorAll: (selector: string) => selector === '[data-e2e="liked-tab"]' ? [tab] : [],
    getElementById: () => panel,
  }) as unknown as Document;
  assert.equal(resolveTiktokScopeRoot(make(inactive), "tt_like"), null);
  assert.equal(resolveTiktokScopeRoot(make(active), "tt_like"), panel);
});


test("hidden auth templates are not evidence but visible walls override stale markers", () => {
  const hidden = new FakeElement({ attrs: { "aria-hidden": "true" } });
  const visible = new FakeElement();
  const doc = { body: { textContent: "" }, querySelector: (selector: string) => {
    if (selector === '[data-e2e="nav-profile"]') return hidden;
    return null;
  } } as unknown as Document;
  assert.equal(detectTiktokAccessState(doc, "https://www.tiktok.com/"), "logged_out");
  const wallDoc = { querySelector: (selector: string) => {
    if (selector === '[data-e2e="nav-profile"]' || selector === '[data-e2e="login-modal"]') return visible;
    return null;
  } } as unknown as Document;
  assert.equal(detectTiktokAccessState(wallDoc, "https://www.tiktok.com/"), "login_wall");
});

test("hidden SSR login translations cannot establish a visible login wall", () => {
  const doc = {
    body: { textContent: '{"login_label":"Log in to TikTok"}', innerText: "" },
    querySelector: () => null,
  } as unknown as Document;
  assert.equal(detectTiktokAccessState(doc, "https://www.tiktok.com/"), "logged_out");
});

// Shape observed in the signed-in desktop profile: selected tab has no
// aria-controls; the owned layout contains a scoped, explicit empty message.
test("live Chinese liked-empty panel requires the selected tab and matching layout", () => {
  const textSelector = "div, span, p, h1, h2, h3";
  const emptySelector = 'main[class*="--MainDetailWrapper"] [class*="--DivErrorContainer"]';
  const make = (text: string, selected = true, hidden = false, inLayout = true) => {
    const empty = new FakeElement({ attrs: hidden ? { "aria-hidden": "true" } : {},
      selectorMap: { [textSelector]: [new FakeElement({ textContent: text })] } });
    const layout = new FakeElement({ selectorMap: { [emptySelector]: inLayout ? [empty] : [] } });
    const tab = new FakeElement({ attrs: { "aria-selected": String(selected) }, closestElement: layout });
    const doc = { querySelectorAll: (selector: string) => selector === '[data-e2e="liked-tab"]' ? [tab] : [] } as unknown as Document;
    return { doc, empty };
  };
  const live = make("还没有赞过任何视频");
  assert.equal(resolveTiktokScopeRoot(live.doc, "tt_like"), live.empty);
  assert.equal(detectTiktokEmptyState(live.empty as unknown as ParentNode, "tt_like"), "还没有赞过任何视频");
  for (const invalid of [make("还没有赞过任何视频", false), make("还没有赞过任何视频", true, true),
    make("还没有赞过任何视频", true, false, false), make("网络错误，请重试"), make("上传你的首发视频")]) {
    assert.equal(resolveTiktokScopeRoot(invalid.doc, "tt_like"), null);
  }
  assert.equal(resolveTiktokScopeRoot(live.doc, "tt_collect"), null);
});

test("live Favorites tab without data-e2e can prove only its scoped affirmative empty", () => {
  const empty = new FakeElement({ selectorMap: { "div, span, p, h1, h2, h3": [new FakeElement({ textContent: "你收藏的作品将显示在这里。" })] } });
  const layout = new FakeElement({ selectorMap: {
    'main[class*="--MainDetailWrapper"] [class*="--DivErrorContainer"]': [empty],
  } });
  const make = (selected: boolean) => ({ querySelectorAll: (selector: string) =>
    selector === '[data-e2e="user-page"] [role="tab"][class*="--PFavorite"]'
      ? [new FakeElement({ attrs: { "aria-selected": String(selected) }, closestElement: layout })] : [],
  }) as unknown as Document;
  assert.equal(resolveTiktokScopeRoot(make(true), "tt_collect"), empty);
  assert.equal(resolveTiktokScopeRoot(make(false), "tt_collect"), null);
  assert.equal(resolveTiktokScopeRoot(make(true), "tt_like"), null);
});

test("live following-empty popup must match viewer and the following-specific message", () => {
  const make = (heading: string, text: string, hidden = false) => {
    const empty = new FakeElement({ selectorMap: { "div, span, p, h1, h2, h3": [new FakeElement({ textContent: text })] } });
    const popup = new FakeElement({ attrs: hidden ? { "aria-hidden": "true" } : {}, selectorMap: {
      h1: [new FakeElement({ textContent: heading })], '[class*="--DivEmptyListContainer"]': [empty],
    } });
    const doc = { querySelectorAll: (selector: string) => selector === '[data-e2e="follow-info-popup"][role="dialog"]' ? [popup] : [] } as unknown as Document;
    return { doc, empty };
  };
  const own = make("@me.user", "此用户关注的人将会出现在这里");
  assert.equal(resolveTiktokScopeRoot(own.doc, "tt_follow", "me.user"), own.empty);
  assert.equal(resolveTiktokScopeRoot(own.doc, "tt_follow"), null);
  for (const invalid of [make("someone.else", "此用户关注的人将会出现在这里"),
    make("me.user", "关注此用户的人将会出现在这里"), make("me.user", ""),
    make("me.user", "此用户关注的人将会出现在这里", true)]) {
    assert.equal(resolveTiktokScopeRoot(invalid.doc, "tt_follow", "me.user"), null);
  }
});
