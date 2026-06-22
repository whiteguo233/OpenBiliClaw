/**
 * YouTube platform adapter for the generic behavior collector.
 */

import type { ActionHint, PageType, PlatformAdapter } from "../types.js";

const WATCH_ID_PATTERN = /[?&]v=([A-Za-z0-9_-]{6,})/;
const SHORTS_ID_PATTERN = /\/shorts\/([A-Za-z0-9_-]{6,})/;
const SHORT_URL_PATTERN = /youtu\.be\/([A-Za-z0-9_-]{6,})/;

const CARD_SELECTOR = [
  "ytd-rich-item-renderer",
  "ytd-video-renderer",
  "ytd-grid-video-renderer",
  "ytd-compact-video-renderer",
  'a[href*="/watch"]',
  'a[href*="/shorts/"]',
].join(",");

const SEARCH_INPUT_SELECTOR = [
  'input[name="search_query"]',
  "ytd-searchbox input",
  'input[type="text"]',
].join(",");

export function detectYoutubePageType(url: string): PageType {
  if (url.includes("/watch") || url.includes("/shorts/")) return "video";
  if (url.includes("/results")) return "search";
  if (url.includes("/@") || url.includes("/channel/") || url.includes("/c/")) {
    return "channel";
  }
  return "home";
}

export function extractYoutubeVideoId(url: string): string | null {
  return (
    url.match(WATCH_ID_PATTERN)?.[1] ??
    url.match(SHORTS_ID_PATTERN)?.[1] ??
    url.match(SHORT_URL_PATTERN)?.[1] ??
    null
  );
}

function normalizeText(value: string | null | undefined): string {
  return (value ?? "").trim();
}

export function inferYoutubeActionType(hint: ActionHint): string | null {
  const text = `${normalizeText(hint.text)} ${normalizeText(hint.ariaLabel)} ${hint.className}`
    .toLowerCase();

  if (!text) return null;
  if (text.includes("dislike") || text.includes("不喜欢") || text.includes("不感兴趣")) {
    return "dislike";
  }
  if (text.includes("like") || text.includes("点赞")) return "like";
  if (text.includes("save") || text.includes("收藏") || text.includes("稍后观看")) {
    return "favorite";
  }
  if (text.includes("comment") || text.includes("评论")) return "comment";
  if (text.includes("share") || text.includes("分享")) return "share";
  if (text.includes("subscribe") || text.includes("订阅") || text.includes("关注")) {
    return "follow";
  }
  return null;
}

export const youtubeAdapter: PlatformAdapter = {
  sourcePlatform: "youtube",
  detectPageType: detectYoutubePageType,
  extractContentId: extractYoutubeVideoId,
  cardSelector: CARD_SELECTOR,
  searchInputSelector: SEARCH_INPUT_SELECTOR,
  videoSelector: "video",
  inferActionType: inferYoutubeActionType,
  buildEventMetadata(url: string): Record<string, unknown> {
    return { video_id: extractYoutubeVideoId(url) };
  },
};
