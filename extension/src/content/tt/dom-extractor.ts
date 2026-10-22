/**
 * TikTok DOM-driven account-behavior extractor.
 *
 * Pure Document → normalized-row helpers for the tt_like / tt_collect /
 * tt_history / tt_follow scopes. TikTok's signed web API is deliberately
 * not replayed (no X-Bogus reconstruction), so these extractors read
 * whatever the signed-in page rendered. Selectors are layered:
 * data-e2e attributes first (most stable across TikTok web releases),
 * href shapes next, visible-text fallbacks last. Missing fields default
 * to empty string / omitted counts — the sink drops rows without an
 * identity, and the completion classifier refuses to call an
 * unrecognized blank page "empty".
 */

import { pickMetricCount } from "../metric-count.ts";
import type { TiktokBootstrapItem, TiktokScope } from "./task-executor.ts";

// ---------------------------------------------------------------------------
// href shape guards
// ---------------------------------------------------------------------------

/** Extract a numeric video id from /@<handle>/video/<id> (or /photo/<id>). */
export function extractTiktokVideoIdFromHref(href: string): string {
  if (!href) return "";
  const match = href.match(/\/(?:video|photo)\/(\d{6,})/);
  return match ? (match[1] ?? "") : "";
}

/** Extract the handle from an /@<handle> href. Returns "" for non-profile paths. */
export function extractTiktokHandleFromHref(href: string): string {
  if (!href) return "";
  const match = href.match(/(?:^|https?:\/\/[\w.]*tiktok\.com)\/@([\w.-]+)/);
  const handle = match?.[1] ?? "";
  // TikTok handles are 2-24 chars of letters/digits/underscore/dot.
  if (!/^[\w.]{2,24}$/.test(handle)) return "";
  return handle;
}

// ---------------------------------------------------------------------------
// Access-state and empty-state detection
// ---------------------------------------------------------------------------

const LOGIN_WALL_TEXTS: readonly string[] = [
  "log in to tiktok",
  "log in and follow",
  "sign up for tiktok",
  "登录 tiktok",
  "登录后查看",
];

const LOGIN_WALL_SELECTORS: readonly string[] = [
  '[data-e2e="top-login-button"]',
  '[data-e2e="login-modal"]',
  '[data-e2e="login-form"]',
  'form[action*="login"]',
];

// Signed-in evidence: header avatar / upload entry / profile menu. The
// contract requires a positively observed signed-in profile marker —
// cookie presence alone is never evidence, and absence of a marker fails
// closed as logged_out.
const SIGNED_IN_SELECTORS: readonly string[] = [
  '[data-e2e="nav-profile"]',
  '[data-e2e="profile-icon"]',
  '[data-e2e="nav-message"]',
  '[data-e2e="nav-inbox"]',
];

export function isTiktokLoginWallUrl(urlValue: string): boolean {
  try {
    const path = new URL(urlValue, "https://www.tiktok.com").pathname.toLowerCase();
    return (
      path.startsWith("/login") ||
      path.startsWith("/signup") ||
      path.startsWith("/register")
    );
  } catch {
    return /\/(login|signup|register)(\/|$|\?)/i.test(urlValue);
  }
}

/**
 * Classify what the tab currently shows. "signed_in" only on a positive
 * profile-marker observation; a login wall (URL or modal/button) is
 * reported even when no marker is present; anything else fails closed
 * as "logged_out".
 */
export function detectTiktokAccessState(
  doc: Document,
  urlValue: string,
): "signed_in" | "login_wall" | "logged_out" {
  if (isTiktokLoginWallUrl(urlValue)) return "login_wall";
  for (const sel of LOGIN_WALL_SELECTORS) {
    const element = doc.querySelector<HTMLElement>(sel);
    if (element && isTiktokElementVisible(element)) return "login_wall";
  }
  for (const sel of SIGNED_IN_SELECTORS) {
    const element = doc.querySelector<HTMLElement>(sel);
    if (element && isTiktokElementVisible(element)) return "signed_in";
  }
  // textContent includes SSR JSON, translation dictionaries and hidden login
  // templates. Only rendered text can establish a visible login wall.
  const bodyText = (doc.body?.innerText ?? "").toLowerCase();
  for (const phrase of LOGIN_WALL_TEXTS) {
    if (bodyText.includes(phrase)) return "login_wall";
  }
  return "logged_out";
}

// Per-scope recognized empty states. Only these phrases may produce a
// valid "empty" classification — anything else zero-row stays failed.
const EMPTY_STATE_TEXTS: Record<TiktokScope, readonly string[]> = {
  tt_like: [
    "no liked videos yet",
    "liked videos will appear here",
    "还没有喜欢的视频",
    "暂无喜欢的视频",
    "还没有赞过任何视频",
    "赞过的视频将会显示在这里",
  ],
  tt_collect: [
    "no favorite videos yet",
    "no favorites yet",
    "favorites will appear here",
    "还没有收藏的视频",
    "暂无收藏",
    "你收藏的作品将显示在这里",
  ],
  tt_history: [
    "no watch history",
    "watch history is empty",
    "you haven't watched",
    "暂无观看历史",
    "观看历史为空",
  ],
  tt_follow: [
    "not following anyone",
    "no accounts followed",
    "还没有关注任何人",
    "暂无关注",
    "此用户关注的人将会出现在这里",
  ],
};

/**
 * Return the matched empty-state phrase when the document positively
 * shows this scope's logged-in empty state, else "". Matching is
 * limited to short text-bearing elements so a long description that
 * happens to contain a phrase doesn't false-trigger.
 */
export function detectTiktokEmptyState(doc: ParentNode, scope: TiktokScope): string {
  const phrases = EMPTY_STATE_TEXTS[scope];
  const candidates = Array.from(
    doc.querySelectorAll<HTMLElement>("div, span, p, h1, h2, h3"),
  );
  for (const el of candidates) {
    const text = (el.textContent ?? "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!text || text.length > 60 || !isTiktokElementVisible(el)) continue;
    for (const phrase of phrases) {
      if (text.includes(phrase)) return phrase;
    }
  }
  return "";
}

// ---------------------------------------------------------------------------
// Per-card pickers
// ---------------------------------------------------------------------------

function findCardContainer(target: HTMLElement): HTMLElement {
  const card = target.closest<HTMLElement>(
    [
      "[data-e2e]",
      'div[class*="DivItemContainer"]',
      'div[class*="item-container"]',
      'div[class*="video-card"]',
      'div[class*="user-card"]',
      'div[class*="follow-card"]',
      "li",
      "article",
      "section",
    ].join(","),
  );
  return card ?? target;
}

function pickCardTitle(card: HTMLElement, target: HTMLElement): string {
  const aria = target.getAttribute("aria-label")?.trim() ?? "";
  if (aria) return aria;
  const title = target.getAttribute("title")?.trim() ?? "";
  if (title) return title;
  const candidates = [
    '[data-e2e="video-desc"]',
    '[data-e2e="search-card-video-caption"]',
    '[class*="video-title"]',
    '[class*="title"]',
    '[class*="desc"]',
    "p",
  ];
  for (const sel of candidates) {
    const el = card.querySelector<HTMLElement>(sel);
    const text = el?.textContent?.trim() ?? "";
    if (text) return text;
  }
  return target.textContent?.trim() ?? "";
}

function pickAuthorHandleFromCard(card: HTMLElement): string {
  const anchors = Array.from(
    card.querySelectorAll<HTMLAnchorElement>('a[href*="/@"]'),
  );
  for (const anchor of anchors) {
    const handle = extractTiktokHandleFromHref(
      anchor.getAttribute("href") ?? anchor.href ?? "",
    );
    if (handle) return handle;
  }
  const handleSpans = Array.from(card.querySelectorAll<HTMLElement>("span, p"))
    .map((el) => el.textContent?.replace(/\s+/g, " ").trim() ?? "")
    .filter((text) => /^@[\w.]{2,24}$/.test(text))
    .map((text) => text.slice(1));
  return handleSpans[0] ?? "";
}

function pickCoverUrl(card: HTMLElement): string {
  const img = card.querySelector<HTMLImageElement>("img");
  if (!img) return "";
  return (
    img.getAttribute("src") ||
    img.getAttribute("data-src") ||
    img.getAttribute("data-original") ||
    ""
  );
}

function pickCardMetrics(card: HTMLElement): Pick<
  TiktokBootstrapItem,
  "view_count" | "like_count" | "collect_count" | "comment_count" | "share_count"
> {
  const view_count = pickMetricCount(card, ["views", "view", "播放", "观看"]);
  const like_count = pickMetricCount(card, ["likes", "like", "点赞", "喜欢"]);
  const collect_count = pickMetricCount(card, ["favorites", "favorite", "收藏"]);
  const comment_count = pickMetricCount(card, ["comments", "comment", "评论"]);
  const share_count = pickMetricCount(card, ["shares", "share", "分享"]);
  return {
    ...(view_count > 0 ? { view_count } : {}),
    ...(like_count > 0 ? { like_count } : {}),
    ...(collect_count > 0 ? { collect_count } : {}),
    ...(comment_count > 0 ? { comment_count } : {}),
    ...(share_count > 0 ? { share_count } : {}),
  };
}

// ---------------------------------------------------------------------------
// Public extractors
// ---------------------------------------------------------------------------

/**
 * Harvest the scope's rows from the current document. Video scopes
 * (tt_like / tt_collect / tt_history) key on /@handle/video|photo/<id>
 * anchors; tt_follow keys on profile anchors (/@handle) and skips any
 * anchor that also points at a video (author chips inside video cards).
 */
export function extractTiktokItemsFromDocument(
  doc: ParentNode,
  scope: TiktokScope,
  baseUrl: string,
  maxItems: number,
): TiktokBootstrapItem[] {
  const cap = Math.max(0, Math.floor(maxItems));
  if (cap === 0) return [];
  if (scope === "tt_follow") return extractFollowItems(doc, scope, baseUrl, cap);
  return extractVideoItems(doc, scope, baseUrl, cap);
}

function extractVideoItems(
  doc: ParentNode,
  scope: TiktokScope,
  baseUrl: string,
  cap: number,
): TiktokBootstrapItem[] {
  const items: TiktokBootstrapItem[] = [];
  const seen = new Set<string>();
  const targets = Array.from(
    doc.querySelectorAll<HTMLElement>('a[href*="/video/"], a[href*="/photo/"]'),
  );
  for (const target of targets) {
    if (items.length >= cap) break;
    const href = target.getAttribute("href") ?? "";
    const videoId = extractTiktokVideoIdFromHref(href);
    if (!videoId || seen.has(videoId) || !isTiktokElementVisible(target)) continue;
    seen.add(videoId);

    const card = findCardContainer(target);
    const handle =
      extractTiktokHandleFromHref(href) || pickAuthorHandleFromCard(card);
    items.push({
      scope,
      video_id: videoId,
      url: handle
        ? `https://www.tiktok.com/@${handle}/video/${videoId}`
        : absolutize(href, baseUrl),
      title: pickCardTitle(card, target),
      author_handle: handle,
      author: handle,
      cover_url: pickCoverUrl(card),
      ...pickCardMetrics(card),
    });
  }
  return items;
}

function extractFollowItems(
  doc: ParentNode,
  scope: TiktokScope,
  baseUrl: string,
  cap: number,
): TiktokBootstrapItem[] {
  const items: TiktokBootstrapItem[] = [];
  const seen = new Set<string>();
  const anchors = Array.from(
    doc.querySelectorAll<HTMLAnchorElement>('a[href*="/@"]'),
  );
  for (const anchor of anchors) {
    if (items.length >= cap) break;
    const href = anchor.getAttribute("href") ?? anchor.href ?? "";
    // Skip video/photo links (author chips on video cards) and hashtag links.
    if (extractTiktokVideoIdFromHref(href)) continue;
    const handle = extractTiktokHandleFromHref(href);
    if (!handle || seen.has(handle) || !isTiktokElementVisible(anchor)) continue;
    seen.add(handle);

    const card = findCardContainer(anchor);
    const nickname =
      pickCardTitle(card, anchor) || anchor.textContent?.trim() || "";
    items.push({
      scope,
      video_id: "",
      url: absolutize(`/@${handle}`, baseUrl),
      title: nickname,
      author_handle: handle,
      author: nickname || handle,
      // DOM extraction cannot see secUid; the handle is the stable
      // follow-scope identity the backend dedupes on.
      creator_sec_uid: handle,
      cover_url: pickCoverUrl(card),
    });
  }
  return items;
}

// A container must overflow its viewport by more than this to count as
// scrollable — sub-pixel layout rounding can leave scrollHeight a hair
// above clientHeight on non-scrolling wrappers.
const SCROLLABLE_OVERFLOW_TOLERANCE_PX = 4;

/**
 * Find the inner scrollable container hosting the current list (TikTok
 * profile tabs and the following/watch-history modals are virtualized
 * inner lists; window scrolling often triggers no pagination). Returns
 * null when none is found — callers fall back to window scrolling.
 */
export function pickTiktokScrollTarget(doc: HTMLElement): Element | null {
  const anchor =
    doc.querySelector<HTMLElement>('a[href*="/video/"]') ??
    doc.querySelector<HTMLElement>('a[href*="/@"]');
  if (!anchor) return null;
  const view = doc.ownerDocument?.defaultView ?? null;
  let node: Element | null = anchor.parentElement;
  while (node) {
    if (isScrollableContainer(node, view)) return node;
    if (node === doc) break;
    node = node.parentElement;
  }
  return null;
}

function isScrollableContainer(el: Element, view: Window | null): boolean {
  let overflowY = "";
  try {
    overflowY = view?.getComputedStyle?.(el)?.overflowY ?? "";
  } catch {
    return false;
  }
  if (overflowY !== "auto" && overflowY !== "scroll") return false;
  const scrollHeight = Number(el.scrollHeight ?? 0);
  const clientHeight = Number(el.clientHeight ?? 0);
  return scrollHeight > clientHeight + SCROLLABLE_OVERFLOW_TOLERANCE_PX;
}

function absolutize(href: string, baseUrl: string): string {
  if (!href) return "";
  if (/^https?:\/\//.test(href)) return href;
  try {
    return new URL(href, baseUrl).toString();
  } catch {
    return href;
  }
}

/** Hidden templates and inactive panels are not account evidence. */
export function isTiktokElementVisible(element: HTMLElement): boolean {
  const view = element.ownerDocument?.defaultView;
  let current: HTMLElement | null = element;
  while (current) {
    if (current.hidden || current.getAttribute("aria-hidden") === "true" ||
        current.hasAttribute?.("inert") || current.style?.display === "none" ||
        current.style?.visibility === "hidden") return false;
    const style = view?.getComputedStyle(current);
    if (style?.display === "none" || style?.visibility === "hidden") return false;
    current = current.parentElement;
  }
  return true;
}

/** Own-profile evidence must name the positively identified session viewer. */
export function isTiktokOwnProfile(url: string, handle: string): boolean {
  if (!/^[\w.]{2,24}$/.test(handle)) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" &&
      (parsed.hostname === "tiktok.com" || parsed.hostname.endsWith(".tiktok.com")) &&
      parsed.pathname.replace(/\/$/, "").toLowerCase() === `/@${handle.toLowerCase()}`;
  } catch { return false; }
}

export const TIKTOK_SCOPE_TAB_SELECTORS: Record<TiktokScope, readonly string[]> = {
  tt_like: ['[data-e2e="liked-tab"]', '[data-e2e="user-liked-tab"]', '[data-e2e="user-tab-liked"]'],
  tt_collect: ['[data-e2e="favorites-tab"]', '[data-e2e="user-favorites-tab"]', '[data-e2e="user-tab-favorites"]', '[data-e2e="user-page"] [role="tab"][class*="--PFavorite"]'],
  tt_follow: [], tt_history: [],
};
const SCOPE_ROOT_SELECTORS: Record<TiktokScope, string> = {
  tt_like: '[data-e2e="user-liked-item-list"], [data-e2e="liked-video-list"]',
  tt_collect: '[data-e2e="user-favorites-item-list"], [data-e2e="favorite-video-list"]',
  tt_follow: '[data-e2e="following-list"], [data-e2e="following-list-container"]',
  tt_history: '[data-e2e="watch-history-list"], [data-e2e="history-video-list"]',
};

/** Require an identifiable active list; never use document/main/generic modal. */
export function resolveTiktokScopeRoot(
  doc: Document, scope: TiktokScope, expectedViewer = "",
): HTMLElement | null {
  const roots = new Set<HTMLElement>(
    Array.from(doc.querySelectorAll<HTMLElement>(SCOPE_ROOT_SELECTORS[scope])).filter(isTiktokElementVisible),
  );
  for (const selector of TIKTOK_SCOPE_TAB_SELECTORS[scope]) {
    for (const tab of Array.from(doc.querySelectorAll<HTMLElement>(selector))) {
      if (!isTiktokElementVisible(tab) || tab.getAttribute("aria-selected") !== "true") continue;
      const panelId = tab.getAttribute("aria-controls");
      const panel = panelId ? doc.getElementById(panelId) : null;
      if (panel && panel.getAttribute("role") === "tabpanel" && isTiktokElementVisible(panel)) roots.add(panel);
      // The live desktop profile uses aria-selected without aria-controls.
      // Its explicit empty message is scoped to the same profile layout as
      // the selected tab. Never treat a generic main/error box as a list.
      if (!panelId && (scope === "tt_like" || scope === "tt_collect")) {
        const layout = tab.closest<HTMLElement>('[class*="--DivShareLayoutMain"]');
        for (const empty of Array.from(layout?.querySelectorAll<HTMLElement>(
          'main[class*="--MainDetailWrapper"] [class*="--DivErrorContainer"]',
        ) ?? [])) {
          if (isTiktokElementVisible(empty) && detectTiktokEmptyState(empty, scope) &&
              !empty.querySelector('a[href*="/video/"], a[href*="/photo/"]')) roots.add(empty);
        }
      }
    }
  }
  if (scope === "tt_follow" && /^[\w.]{2,24}$/.test(expectedViewer)) {
    for (const popup of Array.from(doc.querySelectorAll<HTMLElement>(
      '[data-e2e="follow-info-popup"][role="dialog"]',
    ))) {
      const heading = popup.querySelector<HTMLElement>("h1");
      if (!isTiktokElementVisible(popup) || !heading || !isTiktokElementVisible(heading) ||
          (heading.textContent ?? "").trim().replace(/^@/, "").toLowerCase() !== expectedViewer.toLowerCase()) continue;
      for (const empty of Array.from(popup.querySelectorAll<HTMLElement>('[class*="--DivEmptyListContainer"]'))) {
        // This exact message names following, whereas the adjacent followers
        // tab has different text. A blank popup never counts as empty.
        if (isTiktokElementVisible(empty) && detectTiktokEmptyState(empty, scope) &&
            !empty.querySelector('a[href*="/@"]')) roots.add(empty);
      }
    }
  }
  const narrow = [...roots].filter((root) => ![...roots].some((other) => other !== root && root.contains(other)));
  return narrow.length === 1 ? narrow[0]! : null;
}
