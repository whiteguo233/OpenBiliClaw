/**
 * YouTube platform adapter for the generic behavior collector.
 */

import type { ActionHint, PageType, PlatformAdapter } from "../types.js";
import {
  actionClassTokens,
  matchesWord,
  shortActionLabel,
} from "../behavior.ts";
import { queryParam } from "./search-query.ts";

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
  // #205 calibration for YouTube's English DOM, verified against live pages:
  // real control labels are verb-first ("Like this video along with 1,234
  // others", "Dislike this video", "Save to playlist", "Share", "Subscribe"),
  // while search/result card anchors carry aria-label = "<full video title> by
  // <author> …" — a title merely CONTAINING "dislike" must not fire. So the
  // aria-label surface is matched with a verb-first anchor, card copy is
  // length-capped, and class names match per token. zh UI labels are also
  // verb-first ("喜欢此视频" / "不喜欢此视频" / "保存至播放列表").
  const text = `${shortActionLabel(hint.text, 12)} ${normalizeText(hint.ariaLabel)}`
    .toLowerCase();
  const aria = normalizeText(hint.ariaLabel).toLowerCase();
  const classes = actionClassTokens(hint.className);
  if (!text.trim() && classes.length === 0) return null;
  const hasToken = (keyword: string): boolean =>
    classes.some((token) => token.includes(keyword));
  const ariaAnchored = (...verbs: string[]): boolean =>
    verbs.some((verb) => aria.startsWith(verb));
  if (
    ariaAnchored("dislike", "不喜欢", "不感兴趣") ||
    hasToken("dislike") ||
    (!aria && text.includes("dislike")) ||
    (!aria && (text.includes("不喜欢") || text.includes("不感兴趣")))
  ) {
    return "dislike";
  }
  if (
    ariaAnchored("like", "点赞", "喜欢此视频", "赞") ||
    hasToken("like") ||
    (!aria && (matchesWord(text, "like") || text.includes("点赞")))
  ) {
    return "like";
  }
  if (
    ariaAnchored("save", "保存", "稍后观看") ||
    hasToken("save") ||
    (!aria && (matchesWord(text, "save") || text.includes("收藏") || text.includes("稍后观看")))
  ) {
    return "favorite";
  }
  if (
    ariaAnchored("comment", "评论") ||
    hasToken("comment") ||
    (!aria && (matchesWord(text, "comment") || text.includes("评论")))
  ) {
    return "comment";
  }
  if (
    ariaAnchored("share", "分享") ||
    hasToken("share") ||
    (!aria && (matchesWord(text, "share") || text.includes("分享")))
  ) {
    return "share";
  }
  if (
    ariaAnchored("subscribe", "订阅", "关注") ||
    hasToken("subscribe") ||
    (!aria && (matchesWord(text, "subscribe") || text.includes("订阅") || text.includes("关注")))
  ) {
    return "follow";
  }
  return null;
}

export const youtubeAdapter: PlatformAdapter = {
  sourcePlatform: "youtube",
  detectPageType: detectYoutubePageType,
  extractContentId: extractYoutubeVideoId,
  extractSearchQuery: (url) => queryParam(url, "search_query"),
  cardSelector: CARD_SELECTOR,
  searchInputSelector: SEARCH_INPUT_SELECTOR,
  videoSelector: "video",
  dwellPageTypes: ["video"],
  inferActionType: inferYoutubeActionType,
  buildEventMetadata(url: string): Record<string, unknown> {
    return { video_id: extractYoutubeVideoId(url) };
  },
};
