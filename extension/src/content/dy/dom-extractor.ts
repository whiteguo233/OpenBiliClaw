/**
 * Douyin DOM-driven bootstrap extractor.
 *
 * Why this exists: Douyin's user-tab routes (作品 / 喜欢 / 收藏 /
 * 关注) are React-Router driven and frequently re-render WITHOUT
 * issuing a fresh /aweme/v1/web/aweme/<scope>/ XHR — verified
 * empirically (2026-05-08 e2e + url_probe). The MAIN-world XHR/fetch
 * tap captures the few requests that *do* fire (mostly the initial
 * landing) but misses everything served from React state. DOM
 * extraction harvests whatever is rendered RIGHT NOW, mirroring the
 * approach the XHS bootstrap takes.
 *
 * Used in two ways:
 *   1. Standalone for scopes 喜欢/收藏 where XHR rarely fires.
 *   2. As a safety net at the end of every runScope pass — items
 *      collected here merge with XHR items, deduped by aweme_id /
 *      creator_sec_uid.
 *
 * Selectors are deliberately tolerant: each anchor type uses href
 * shape as the primary signal (it's the most stable Douyin contract),
 * and per-card metadata pickers fall through several class-name
 * heuristics. Missing fields default to empty string — downstream
 * consumers tolerate empty fields via the existing
 * BootstrapItemSink filter (sees aweme_id or creator_sec_uid).
 */

import type {
  DouyinBootstrapItem,
  DouyinScope,
  DouyinSearchItem,
} from "../../main/dy-fetch-tap.ts";
import { pickMetricCount } from "../metric-count.ts";

// ---------------------------------------------------------------------------
// href shape guards
// ---------------------------------------------------------------------------

/**
 * Extract an aweme_id from a /video/<id> URL. Returns "" when the
 * href doesn't point at a video page.
 */
export function extractAwemeIdFromHref(href: string): string {
  if (!href) return "";
  const match = href.match(/\/video\/(\d+)/);
  return match ? (match[1] ?? "") : "";
}

/**
 * Extract a sec_uid from a /user/<sec_uid> URL. Returns "" when the
 * href doesn't point at a user page or points at /user/self (which
 * is the viewing user, not a followed creator).
 */
export function extractSecUidFromHref(href: string): string {
  if (!href) return "";
  // /user/self isn't a meaningful follow target.
  if (/\/user\/self(\?|$)/.test(href)) return "";
  // Real sec_uid starts with MS4w (douyin's base64-like prefix).
  const match = href.match(/\/user\/(MS4w[\w-]+)/);
  return match ? (match[1] ?? "") : "";
}

// ---------------------------------------------------------------------------
// Pickers — per-card text/image extraction with selector fallbacks
// ---------------------------------------------------------------------------

/**
 * Find the closest "card" container for an anchor. Tries common
 * Douyin class-name fragments first, falls back to the anchor itself
 * if no parent matches (which still gives the pickers something to
 * search inside).
 */
function findCardContainer(anchor: HTMLElement): HTMLElement {
  const card = anchor.closest<HTMLElement>(
    [
      'li[class*="ec-card"]',
      'li[class*="card"]',
      'div[class*="ec-card"]',
      'div[class*="card-wrap"]',
      'div[class*="aweme-card"]',
      'div[class*="user-card"]',
      'div[class*="follow-card"]',
      'div[class*="cover-wrap"]',
      "li",
      "article",
      "section",
    ].join(","),
  );
  return card ?? anchor;
}

function pickCardTitle(card: HTMLElement, anchor: HTMLAnchorElement): string {
  // Anchor's own aria-label / title is often the cleanest source.
  const aria = anchor.getAttribute("aria-label")?.trim() ?? "";
  if (aria) return aria;
  const title = anchor.getAttribute("title")?.trim() ?? "";
  if (title) return title;

  const candidates = [
    'p[class*="title"]',
    'div[class*="title"]',
    'span[class*="title"]',
    'p[class*="desc"]',
    'div[class*="desc"]',
    'span[class*="desc"]',
    "p",
  ];
  for (const sel of candidates) {
    const el = card.querySelector<HTMLElement>(sel);
    const text = el?.textContent?.trim() ?? "";
    if (text) return text;
  }
  // Last resort: first non-empty text node inside the anchor.
  return anchor.textContent?.trim() ?? "";
}

function pickAuthorName(card: HTMLElement): string {
  const candidates = [
    '[class*="author-name"]',
    '[class*="user-name"]',
    '[class*="nickname"]',
    '[class*="author"] [class*="name"]',
  ];
  for (const sel of candidates) {
    const el = card.querySelector<HTMLElement>(sel);
    const text = el?.textContent?.trim() ?? "";
    if (text) return text;
  }
  return "";
}

function pickAuthorSecUid(card: HTMLElement): string {
  // Look for any /user/MS4w... anchor inside the card (typically the
  // author chip). Avoid matching the card's own primary anchor when
  // it IS a user link (handled by caller for dy_follow).
  const anchors = Array.from(
    card.querySelectorAll<HTMLAnchorElement>('a[href*="/user/MS4w"]'),
  );
  for (const a of anchors) {
    const secUid = extractSecUidFromHref(a.getAttribute("href") ?? a.href ?? "");
    if (secUid) return secUid;
  }
  return "";
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
  DouyinSearchItem,
  "view_count" | "like_count" | "collect_count" | "comment_count" | "share_count"
> {
  const view_count = pickMetricCount(card, ["播放", "观看", "浏览", "view", "play"]);
  const like_count = pickMetricCount(card, ["点赞", "获赞", "赞", "like"]);
  const collect_count = pickMetricCount(card, ["收藏", "collect", "save"]);
  const comment_count = pickMetricCount(card, ["评论", "comment"]);
  const share_count = pickMetricCount(card, ["分享", "share"]);
  return {
    ...(view_count > 0 ? { view_count } : {}),
    ...(like_count > 0 ? { like_count } : {}),
    ...(collect_count > 0 ? { collect_count } : {}),
    ...(comment_count > 0 ? { comment_count } : {}),
    ...(share_count > 0 ? { share_count } : {}),
  };
}

// ---------------------------------------------------------------------------
// Public extractor
// ---------------------------------------------------------------------------

/**
 * Walk the document for cards matching the requested scope and
 * return normalized DouyinBootstrapItem[]. Caps results at maxItems
 * to keep the merge pass cheap.
 *
 * For dy_post / dy_like / dy_collect: anchors with href containing
 * /video/<digits>. The aweme_id is the digit run.
 *
 * For dy_follow: anchors with href starting at /user/MS4w (real
 * follow targets — /user/self is filtered out).
 */
export function extractDouyinItemsFromDocument(
  doc: Document,
  scope: DouyinScope,
  baseUrl: string,
  maxItems: number,
): DouyinBootstrapItem[] {
  const cap = Math.max(0, Math.floor(maxItems));
  if (cap === 0) return [];

  if (scope === "dy_follow") {
    return extractFollowItems(doc, baseUrl, cap);
  }
  return extractVideoItems(doc, scope, baseUrl, cap);
}

export function extractDouyinSearchItemsFromDocument(
  doc: Document,
  baseUrl: string,
  maxItems: number,
): DouyinSearchItem[] {
  const cap = Math.max(0, Math.floor(maxItems));
  if (cap === 0) return [];

  const items: DouyinSearchItem[] = [];
  const seen = new Set<string>();
  const anchors = Array.from(
    doc.querySelectorAll<HTMLAnchorElement>('a[href*="/video/"]'),
  );
  for (const anchor of anchors) {
    if (items.length >= cap) break;
    const href = anchor.getAttribute("href") ?? anchor.href ?? "";
    const awemeId = extractAwemeIdFromHref(href);
    if (!awemeId || seen.has(awemeId)) continue;
    seen.add(awemeId);

    const card = findCardContainer(anchor);
    items.push({
      scope: "dy_search",
      aweme_id: awemeId,
      url: absolutize(href, baseUrl),
      title: pickCardTitle(card, anchor),
      author: pickAuthorName(card),
      author_sec_uid: pickAuthorSecUid(card),
      cover_url: pickCoverUrl(card),
      ...pickCardMetrics(card),
    });
  }
  return items;
}

function extractVideoItems(
  doc: Document,
  scope: DouyinScope,
  baseUrl: string,
  cap: number,
): DouyinBootstrapItem[] {
  const items: DouyinBootstrapItem[] = [];
  const seen = new Set<string>();
  const anchors = Array.from(
    doc.querySelectorAll<HTMLAnchorElement>('a[href*="/video/"]'),
  );
  for (const anchor of anchors) {
    if (items.length >= cap) break;
    const href = anchor.getAttribute("href") ?? anchor.href ?? "";
    const awemeId = extractAwemeIdFromHref(href);
    if (!awemeId || seen.has(awemeId)) continue;
    seen.add(awemeId);

    const card = findCardContainer(anchor);
    const url = absolutize(href, baseUrl);
    items.push({
      scope,
      aweme_id: awemeId,
      creator_sec_uid: "",
      url,
      title: pickCardTitle(card, anchor),
      author: pickAuthorName(card),
      author_sec_uid: pickAuthorSecUid(card),
      cover_url: pickCoverUrl(card),
      ...pickCardMetrics(card),
    });
  }
  return items;
}

function extractFollowItems(
  doc: Document,
  baseUrl: string,
  cap: number,
): DouyinBootstrapItem[] {
  const items: DouyinBootstrapItem[] = [];
  const seen = new Set<string>();
  const anchors = Array.from(
    doc.querySelectorAll<HTMLAnchorElement>('a[href*="/user/MS4w"]'),
  );
  for (const anchor of anchors) {
    if (items.length >= cap) break;
    const href = anchor.getAttribute("href") ?? anchor.href ?? "";
    const secUid = extractSecUidFromHref(href);
    if (!secUid || seen.has(secUid)) continue;
    seen.add(secUid);

    const card = findCardContainer(anchor);
    const nickname = pickAuthorName(card) || anchor.textContent?.trim() || "";
    const cover = pickCoverUrl(card);
    items.push({
      scope: "dy_follow",
      aweme_id: "",
      creator_sec_uid: secUid,
      url: absolutize(href, baseUrl),
      title: nickname,
      author: nickname,
      author_sec_uid: secUid,
      cover_url: cover,
    });
  }
  return items;
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
