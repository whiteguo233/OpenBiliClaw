/**
 * Bilibili platform adapter — selectors, page-type heuristics, and
 * action keywords specific to bilibili.com. Plugged into the generic
 * collector kernel.
 */

import type { ActionHint, PageType, PlatformAdapter } from "../types.js";
import { queryParam } from "./search-query.ts";

const BV_PATTERN = /(BV[0-9A-Za-z]{10})/;

const CARD_SELECTOR = [
  'a[href*="/video/BV"]',
  ".bili-video-card",
  ".video-page-card",
  ".search-all-list .video-item",
  ".feed-card",
].join(",");

const SEARCH_INPUT_SELECTOR =
  'input[type="search"], .nav-search-input, .search-input-el, input[name="keyword"]';

export function detectBilibiliPageType(url: string): PageType {
  if (url.includes("/video/")) return "video";
  if (url.includes("/search")) return "search";
  if (url.includes("space.bilibili.com") || url.includes("/space/")) return "user";
  if (url.includes("/v/")) return "category";
  return "home";
}

export function extractBvid(url: string): string | null {
  return url.match(BV_PATTERN)?.[1] ?? null;
}

function normalizeText(value: string | null | undefined): string {
  return (value ?? "").trim();
}

// Action labels are only trusted when they look like control labels. Bilibili
// video cards carry full descriptions in their textContent AND full video
// titles in their `title` attributes (#205): a passing mention of "不感兴趣" /
// "不喜欢" in either must not turn a card click into a dislike. Every real
// Bilibili control label is tiny (不感兴趣 / 不喜欢 / 减少此类推荐 all ≤ 6 chars),
// so 8 chars is the calibrated ceiling across all label surfaces (#200 used 20,
// which short video-title cards still slipped through).
const LABEL_MAX_CHARS = 8;

function shortLabel(value: string | null | undefined): string {
  const text = normalizeText(value);
  return text.length <= LABEL_MAX_CHARS ? text : "";
}

/** Lowercased class tokens, so keyword matches can't come from one long
 *  concatenated class string that merely contains a keyword as a substring. */
function classTokens(className: string): string[] {
  return className.toLowerCase().split(/\s+/).filter(Boolean);
}

export function inferBilibiliActionType(hint: ActionHint): string | null {
  const text = `${shortLabel(hint.text)} ${shortLabel(hint.ariaLabel)}`
    .toLowerCase();
  const classes = classTokens(hint.className);

  if (!text.trim() && classes.length === 0) return null;
  // English keywords may also live in class names (e.g. "video-dislike") or
  // short control aria-labels ("dislike"); Chinese keywords are meaningful
  // only in visible labels / tooltips.
  const hasToken = (keyword: string): boolean =>
    classes.some((token) => token.includes(keyword));
  if (
    text.includes("不感兴趣") ||
    text.includes("不喜欢") ||
    text.includes("减少此类推荐") ||
    text.includes("减少推荐") ||
    text.includes("dislike") ||
    hasToken("dislike")
  ) {
    return "dislike";
  }
  if (text.includes("点赞") || text.includes("like") || hasToken("like")) return "like";
  if (text.includes("投币") || text.includes("coin") || hasToken("coin")) return "coin";
  if (
    text.includes("收藏") ||
    text.includes("collect") ||
    text.includes("favorite") ||
    hasToken("collect") ||
    hasToken("favorite")
  ) {
    return "favorite";
  }
  if (text.includes("评论") || text.includes("comment") || hasToken("comment")) return "comment";
  if (text.includes("分享") || text.includes("share") || hasToken("share")) return "share";
  if (text.includes("关注") || text.includes("follow") || hasToken("follow")) return "follow";
  return null;
}

export const bilibiliAdapter: PlatformAdapter = {
  sourcePlatform: "bilibili",
  // The MAIN-world interact tap (`main/bili-interact-tap.ts`) emits the
  // authoritative comment / like / favorite / coin / retraction after the
  // corresponding Bilibili write succeeds. The DOM path must not double-count
  // those writes (and cannot infer Bilibili's class-only pressed state).
  // Share / follow have no tap and stay DOM-sourced.
  tapAuthoritativeActions: new Set([
    "comment",
    "like",
    "favorite",
    "coin",
    "retraction",
  ]),
  detectPageType: detectBilibiliPageType,
  extractContentId: extractBvid,
  extractSearchQuery: (url) => queryParam(url, "keyword"),
  cardSelector: CARD_SELECTOR,
  searchInputSelector: SEARCH_INPUT_SELECTOR,
  videoSelector: "video",
  dwellPageTypes: ["video"],
  inferActionType: inferBilibiliActionType,
  buildEventMetadata(url: string): Record<string, unknown> {
    return { bvid: extractBvid(url) };
  },
};
