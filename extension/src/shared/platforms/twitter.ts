/**
 * X (Twitter) platform adapter. Captures the user's own engagement on
 * x.com / twitter.com: like, bookmark (favorite), repost (share), reply
 * (comment). The strong signals come from the MAIN-world GraphQL tap
 * (`main/x-graphql-tap.ts`); this adapter supplies the generic-collector
 * selectors, page-type heuristics, and DOM action-keyword fallbacks.
 *
 * Internal source key is "twitter" everywhere (matching the backend's
 * `source_platform`); the user-facing label "X" is applied server-side.
 */

import type { ActionHint, PageType, PlatformAdapter } from "../types.js";

// Numeric tweet ids in /status/<id> URLs (15-20 digits in practice).
const STATUS_ID_PATTERN = /\/status\/(\d+)/;

const CARD_SELECTOR = [
  'article[data-testid="tweet"]',
  'a[href*="/status/"]',
  'div[data-testid="cellInnerDiv"]',
].join(",");

const SEARCH_INPUT_SELECTOR = [
  'input[data-testid="SearchBox_Search_Input"]',
  'input[aria-label*="Search"]',
  'input[type="search"]',
].join(",");

// Reserved (non-content) top-level paths on x.com — a bare /<segment>
// among these is NOT a user profile.
const RESERVED_TOP_PATHS = new Set([
  "",
  "home",
  "explore",
  "search",
  "notifications",
  "messages",
  "settings",
  "compose",
  "i",
  "hashtag",
  "bookmarks",
  "lists",
  "communities",
  "tos",
  "privacy",
]);

export function detectTwitterPageType(url: string): PageType {
  let pathname = "";
  try {
    pathname = new URL(url).pathname;
  } catch {
    pathname = url;
  }
  if (STATUS_ID_PATTERN.test(pathname)) return "status";
  if (pathname.startsWith("/search") || pathname.startsWith("/explore")) return "search";
  if (pathname === "/" || pathname === "/home") return "home";
  // A single non-reserved path segment is a user profile (/elonmusk).
  const segments = pathname.split("/").filter(Boolean);
  if (segments.length >= 1 && !RESERVED_TOP_PATHS.has(segments[0])) return "profile";
  return "home";
}

export function extractTweetId(url: string): string | null {
  return url.match(STATUS_ID_PATTERN)?.[1] ?? null;
}

function normalizeText(value: string | null | undefined): string {
  return (value ?? "").trim();
}

export function inferTwitterActionType(hint: ActionHint): string | null {
  // X's action controls expose English aria-labels (e.g. "Like",
  // "Bookmark", "Repost", "Share", "Reply") that toggle to "Liked"/"Undo repost"
  // etc. Match on the substring so both states resolve. There is no
  // "coin" concept on X.
  const text = `${normalizeText(hint.text)} ${normalizeText(hint.ariaLabel)} ${hint.className}`
    .toLowerCase();
  if (!text) return null;
  if (text.includes("bookmark")) return "favorite";
  if (text.includes("like")) return "like";
  if (text.includes("share") || text.includes("repost") || text.includes("retweet")) return "share";
  if (text.includes("reply")) return "comment";
  return null;
}

export const twitterAdapter: PlatformAdapter = {
  sourcePlatform: "twitter",
  detectPageType: detectTwitterPageType,
  extractContentId: extractTweetId,
  cardSelector: CARD_SELECTOR,
  searchInputSelector: SEARCH_INPUT_SELECTOR,
  videoSelector: null,
  inferActionType: inferTwitterActionType,
  buildEventMetadata(url: string): Record<string, unknown> {
    return { tweet_id: extractTweetId(url) };
  },
};
