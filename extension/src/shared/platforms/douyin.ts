/**
 * Douyin platform adapter for the generic behavior collector.
 *
 * This complements the bootstrap/task executor: passive page behaviour
 * still flows through `/api/events`, while profile/search harvesting
 * continues to use the existing task-result endpoints.
 */

import type { ActionHint, PageType, PlatformAdapter } from "../types.js";
import {
  actionClassTokens,
  matchesWord,
  shortActionLabel,
} from "../behavior.ts";
import { queryParam, searchPathSegment } from "./search-query.ts";

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
  // Same guard as bilibili (#205): only short control labels are trusted;
  // card copy / video titles must not turn a click into a like/dislike.
  const text = `${shortActionLabel(hint.text)} ${shortActionLabel(hint.ariaLabel)}`
    .toLowerCase();
  const classes = actionClassTokens(hint.className);

  if (!text.trim() && classes.length === 0) return null;
  const hasToken = (keyword: string): boolean =>
    classes.some((token) => token.includes(keyword));
  if (
    text.includes("不感兴趣") ||
    text.includes("不喜欢") ||
    text.includes("减少推荐") ||
    text.includes("dislike") ||
    hasToken("dislike")
  ) {
    return "dislike";
  }
  if (text.includes("点赞") || text.includes("like") || hasToken("like")) return "like";
  if (
    text.includes("收藏") ||
    text.includes("favorite") ||
    text.includes("collect") ||
    hasToken("favorite") ||
    hasToken("collect")
  ) {
    return "favorite";
  }
  if (text.includes("评论") || text.includes("comment") || hasToken("comment")) return "comment";
  if (text.includes("分享") || text.includes("share") || hasToken("share")) return "share";
  if (text.includes("关注") || text.includes("follow") || hasToken("follow")) return "follow";
  return null;
}

export const douyinAdapter: PlatformAdapter = {
  sourcePlatform: "douyin",
  detectPageType: detectDouyinPageType,
  extractContentId: extractAwemeId,
  extractSearchQuery: (url) => searchPathSegment(url) ?? queryParam(url, "keyword"),
  cardSelector: CARD_SELECTOR,
  searchInputSelector: SEARCH_INPUT_SELECTOR,
  videoSelector: "video",
  dwellPageTypes: ["video"],
  inferActionType: inferDouyinActionType,
  buildEventMetadata(url: string): Record<string, unknown> {
    return { aweme_id: extractAwemeId(url) };
  },
};
