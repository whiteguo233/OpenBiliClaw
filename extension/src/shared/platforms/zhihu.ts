/**
 * Zhihu platform adapter for the generic behavior collector.
 */

import type { ActionHint, PageType, PlatformAdapter } from "../types.js";
import {
  actionClassTokens,
  shortActionLabel,
} from "../behavior.ts";
import { queryParam } from "./search-query.ts";

const ANSWER_PATTERN = /zhihu\.com\/question\/(\d+)\/answer\/(\d+)/;
const QUESTION_PATTERN = /zhihu\.com\/question\/(\d+)(?:[/?#]|$)/;
const ARTICLE_PATTERN = /zhuanlan\.zhihu\.com\/p\/(\d+)/;

const CARD_SELECTOR = [
  ".ContentItem",
  ".Question-mainColumn",
  ".SearchResult-Card",
  ".TopstoryItem",
  ".List-item",
  'a[href*="/question/"]',
  'a[href*="zhuanlan.zhihu.com/p/"]',
].join(",");

const SEARCH_INPUT_SELECTOR = [
  'input.SearchBar-input',
  'input[placeholder*="搜索"]',
  'input[type="search"]',
  'input[type="text"]',
].join(",");

export function detectZhihuPageType(url: string): PageType {
  if (ANSWER_PATTERN.test(url)) return "answer";
  if (ARTICLE_PATTERN.test(url)) return "article";
  if (QUESTION_PATTERN.test(url)) return "question";
  if (url.includes("/search")) return "search";
  if (url.includes("/people/")) return "profile";
  if (url.includes("/collection/") || url.includes("/collections/")) return "collection";
  return "home";
}

export function extractZhihuContentId(url: string): string | null {
  const answer = url.match(ANSWER_PATTERN);
  if (answer?.[2]) return `answer:${answer[2]}`;
  const article = url.match(ARTICLE_PATTERN);
  if (article?.[1]) return `article:${article[1]}`;
  const question = url.match(QUESTION_PATTERN);
  if (question?.[1]) return `question:${question[1]}`;
  return null;
}

function normalizeText(value: string | null | undefined): string {
  return (value ?? "").trim();
}

export function inferZhihuActionType(hint: ActionHint): string | null {
  // Same guard as bilibili (#205): only short control labels are trusted.
  // Zhihu answers/cards carry long copy where 喜欢 / 不感兴趣 mentions must not
  // become like/dislike signals; real controls are 赞同 / 反对 / 收藏 (≤ 2 chars).
  const text = `${shortActionLabel(hint.text)} ${shortActionLabel(hint.ariaLabel)}`.toLowerCase();
  const classes = actionClassTokens(hint.className);
  if (!text.trim() && classes.length === 0) return null;
  if (text.includes("反对") || text.includes("不感兴趣")) return "dislike";
  if (text.includes("赞同") || text.includes("喜欢")) return "like";
  if (text.includes("收藏")) return "favorite";
  if (text.includes("评论")) return "comment";
  if (text.includes("分享")) return "share";
  if (text.includes("关注")) return "follow";
  return null;
}

export const zhihuAdapter: PlatformAdapter = {
  sourcePlatform: "zhihu",
  detectPageType: detectZhihuPageType,
  extractContentId: extractZhihuContentId,
  extractSearchQuery: (url) => queryParam(url, "q"),
  cardSelector: CARD_SELECTOR,
  searchInputSelector: SEARCH_INPUT_SELECTOR,
  videoSelector: null,
  dwellPageTypes: ["answer", "article", "question"],
  inferActionType: inferZhihuActionType,
  buildEventMetadata(url: string): Record<string, unknown> {
    const contentId = extractZhihuContentId(url);
    if (!contentId) return {};
    const [content_type, content_id] = contentId.split(":", 2);
    return { content_type, content_id };
  },
};
