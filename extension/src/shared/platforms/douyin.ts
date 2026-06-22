/**
 * Douyin platform adapter for the generic behavior collector.
 *
 * This complements the bootstrap/task executor: passive page behaviour
 * still flows through `/api/events`, while profile/search harvesting
 * continues to use the existing task-result endpoints.
 */

import type { ActionHint, PageType, PlatformAdapter } from "../types.js";

const AWEME_ID_PATTERN = /\/video\/(\d{8,})/;

const CARD_SELECTOR = [
  'a[href*="/video/"]',
  'div[data-e2e*="feed"]',
  'div[data-e2e*="video"]',
  'li[class*="video"]',
].join(",");

const SEARCH_INPUT_SELECTOR =
  'input[type="search"], input[placeholder*="搜索"], input[data-e2e*="search"]';

export function detectDouyinPageType(url: string): PageType {
  if (url.includes("/video/")) return "video";
  if (url.includes("/search")) return "search";
  if (url.includes("/user/")) return "user";
  return "home";
}

export function extractAwemeId(url: string): string | null {
  return url.match(AWEME_ID_PATTERN)?.[1] ?? null;
}

function normalizeText(value: string | null | undefined): string {
  return (value ?? "").trim();
}

export function inferDouyinActionType(hint: ActionHint): string | null {
  const text = `${normalizeText(hint.text)} ${normalizeText(hint.ariaLabel)} ${hint.className}`
    .toLowerCase();

  if (!text) return null;
  if (
    text.includes("不感兴趣") ||
    text.includes("不喜欢") ||
    text.includes("减少推荐") ||
    text.includes("dislike")
  ) {
    return "dislike";
  }
  if (text.includes("点赞") || text.includes("like")) return "like";
  if (text.includes("收藏") || text.includes("favorite") || text.includes("collect")) {
    return "favorite";
  }
  if (text.includes("评论") || text.includes("comment")) return "comment";
  if (text.includes("分享") || text.includes("share")) return "share";
  if (text.includes("关注") || text.includes("follow")) return "follow";
  return null;
}

export const douyinAdapter: PlatformAdapter = {
  sourcePlatform: "douyin",
  detectPageType: detectDouyinPageType,
  extractContentId: extractAwemeId,
  cardSelector: CARD_SELECTOR,
  searchInputSelector: SEARCH_INPUT_SELECTOR,
  videoSelector: "video",
  inferActionType: inferDouyinActionType,
  buildEventMetadata(url: string): Record<string, unknown> {
    return { aweme_id: extractAwemeId(url) };
  },
};
