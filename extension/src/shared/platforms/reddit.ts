/**
 * Reddit platform adapter for the generic behavior collector.
 */

import type { ActionHint, PageType, PlatformAdapter } from "../types.js";

const COMMENT_POST_PATTERN = /(?:reddit\.com\/r\/[^/]+\/comments\/|redd\.it\/)([A-Za-z0-9_]+)/;
const SUBREDDIT_PATTERN = /reddit\.com\/r\/([^/?#]+)/;
const POST_LINK_SELECTOR = 'a[href*="/comments/"],a[href*="redd.it/"]';

const CARD_SELECTOR = [
  'article[data-testid="post-container"]',
  'shreddit-post',
  'a[href*="/comments/"]',
  'div[data-testid="post-container"]',
].join(",");

const SEARCH_INPUT_SELECTOR = [
  'input[type="search"]',
  'input[name="q"]',
  'input[placeholder*="Search"]',
].join(",");

export function detectRedditPageType(url: string): PageType {
  let parsed: URL | null = null;
  try {
    parsed = new URL(url);
  } catch {
    parsed = null;
  }
  const target = parsed?.href ?? url;
  const pathname = parsed?.pathname ?? url;
  if (COMMENT_POST_PATTERN.test(target)) return "post";
  if (pathname.startsWith("/search")) return "search";
  if (SUBREDDIT_PATTERN.test(target)) return "subreddit";
  if (pathname === "/" || pathname === "") return "home";
  return "home";
}

export function extractRedditContentId(url: string): string | null {
  const match = url.match(COMMENT_POST_PATTERN);
  if (!match?.[1]) return null;
  return `t3_${match[1]}`;
}

export function extractRedditSubreddit(url: string): string | null {
  const match = url.match(SUBREDDIT_PATTERN);
  if (!match?.[1]) return null;
  return decodeURIComponent(match[1]);
}

function normalizeText(value: string | null | undefined): string {
  return (value ?? "").trim();
}

export function inferRedditActionType(hint: ActionHint): string | null {
  const text = `${normalizeText(hint.text)} ${normalizeText(hint.ariaLabel)} ${hint.className}`
    .toLowerCase();
  if (!text) return null;
  if (text.includes("downvote")) return "dislike";
  if (text.includes("upvote")) return "like";
  if (text.includes("save") || text.includes("bookmark")) return "favorite";
  if (text.includes("comment") || text.includes("reply")) return "comment";
  if (text.includes("share")) return "share";
  if (text.includes("join") || text.includes("follow")) return "follow";
  return null;
}

function normalizeRedditPostId(value: string | null | undefined): string | null {
  const raw = normalizeText(value);
  if (!raw) return null;
  const withoutThingPrefix = raw.replace(/^thing_/, "");
  const withoutFullname = withoutThingPrefix.replace(/^t3_/, "");
  if (!/^[A-Za-z0-9_]+$/.test(withoutFullname)) return null;
  return `t3_${withoutFullname}`;
}

function elementHref(element: Element | null): string {
  if (!element) return "";
  const href = (element as Element & { href?: unknown }).href;
  if (typeof href === "string" && href.trim()) return href.trim();
  return element.getAttribute("href")?.trim() ?? "";
}

function absolutizeRedditUrl(value: string | null | undefined, currentUrl: string): string {
  const raw = normalizeText(value);
  if (!raw) return "";
  try {
    return new URL(raw, currentUrl || "https://www.reddit.com/").href;
  } catch {
    return raw;
  }
}

function firstAttribute(element: Element | null, names: string[]): string {
  if (!element) return "";
  for (const name of names) {
    const value = element.getAttribute(name)?.trim();
    if (value) return value;
  }
  return "";
}

function metadataFromRedditUrl(url: string): Record<string, unknown> {
  const contentId = extractRedditContentId(url);
  const subreddit = extractRedditSubreddit(url);
  return {
    ...(contentId
      ? {
          content_id: contentId,
          post_id: contentId.replace(/^t3_/, ""),
        }
      : {}),
    ...(subreddit ? { subreddit } : {}),
  };
}

export function buildRedditTargetMetadata(
  target: Element,
  currentUrl: string,
): Record<string, unknown> {
  const card = target.closest(CARD_SELECTOR);
  const directPostLink = target.closest(POST_LINK_SELECTOR);
  const cardPostLink = card?.querySelector(POST_LINK_SELECTOR) ?? null;
  const permalink = firstAttribute(card, [
    "permalink",
    "data-permalink",
    "content-href",
    "data-url",
    "url",
  ]);
  const candidateUrl = absolutizeRedditUrl(
    elementHref(directPostLink) || elementHref(cardPostLink) || permalink,
    currentUrl,
  );
  const urlMetadata = metadataFromRedditUrl(candidateUrl);
  const attrContentId = normalizeRedditPostId(
    firstAttribute(card, [
      "post-id",
      "data-post-id",
      "thingid",
      "thing-id",
      "fullname",
      "data-fullname",
      "name",
      "id",
    ]) ||
      firstAttribute(target, [
        "post-id",
        "data-post-id",
        "thingid",
        "thing-id",
        "fullname",
        "data-fullname",
        "name",
        "id",
      ]),
  );
  const attrSubreddit = firstAttribute(card, [
    "subreddit",
    "subreddit-name",
    "subreddit-prefixed-name",
    "data-subreddit",
  ]).replace(/^r\//i, "");
  const contentId = String(urlMetadata.content_id ?? attrContentId ?? "");
  return {
    ...urlMetadata,
    ...(candidateUrl ? { target_url: candidateUrl } : {}),
    ...(contentId
      ? {
          content_id: contentId,
          post_id: contentId.replace(/^t3_/, ""),
        }
      : {}),
    ...(attrSubreddit && !urlMetadata.subreddit ? { subreddit: attrSubreddit } : {}),
  };
}

export const redditAdapter: PlatformAdapter = {
  sourcePlatform: "reddit",
  detectPageType: detectRedditPageType,
  extractContentId: extractRedditContentId,
  cardSelector: CARD_SELECTOR,
  searchInputSelector: SEARCH_INPUT_SELECTOR,
  videoSelector: null,
  inferActionType: inferRedditActionType,
  buildEventMetadata(url: string): Record<string, unknown> {
    return metadataFromRedditUrl(url);
  },
  buildTargetMetadata: buildRedditTargetMetadata,
};
