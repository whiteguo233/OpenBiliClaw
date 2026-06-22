/**
 * Bilibili platform adapter — selectors, page-type heuristics, and
 * action keywords specific to bilibili.com. Plugged into the generic
 * collector kernel.
 */

import type { ActionHint, PageType, PlatformAdapter } from "../types.js";

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

export function inferBilibiliActionType(hint: ActionHint): string | null {
  const text = `${normalizeText(hint.text)} ${normalizeText(hint.ariaLabel)} ${hint.className}`
    .toLowerCase();

  if (!text) return null;
  if (
    text.includes("不感兴趣") ||
    text.includes("不喜欢") ||
    text.includes("减少此类推荐") ||
    text.includes("减少推荐") ||
    text.includes("dislike")
  ) {
    return "dislike";
  }
  if (text.includes("点赞") || text.includes("like")) return "like";
  if (text.includes("投币") || text.includes("coin")) return "coin";
  if (text.includes("收藏") || text.includes("collect") || text.includes("favorite")) {
    return "favorite";
  }
  if (text.includes("评论") || text.includes("comment")) return "comment";
  if (text.includes("分享") || text.includes("share")) return "share";
  if (text.includes("关注") || text.includes("follow")) return "follow";
  return null;
}

export const bilibiliAdapter: PlatformAdapter = {
  sourcePlatform: "bilibili",
  detectPageType: detectBilibiliPageType,
  extractContentId: extractBvid,
  cardSelector: CARD_SELECTOR,
  searchInputSelector: SEARCH_INPUT_SELECTOR,
  videoSelector: "video",
  inferActionType: inferBilibiliActionType,
  buildEventMetadata(url: string): Record<string, unknown> {
    return { bvid: extractBvid(url) };
  },
};
