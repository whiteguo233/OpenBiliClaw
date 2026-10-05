/**
 * Reddit platform adapter for the generic behavior collector.
 */

import type { ActionHint, PageType, PlatformAdapter } from "../types.js";
import { actionClassTokens, matchesWord } from "../behavior.ts";
import { queryParam } from "./search-query.ts";

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
  // Reddit has no MAIN-world tap, so every action flows through this DOM path.
  // Post titles ride into hint.text, so vote keywords must be anchored to real
  // controls (#205, verified against live search pages): vote arrows/buttons
  // carry the verb in their aria-label or class ("downvote"/"Upvote"), while
  // titles merely containing the word are clicks. Comment/share counts use
  // whole-word matches with plural tolerance ("32 comments").
  const text = `${normalizeText(hint.text)} ${normalizeText(hint.ariaLabel)}`
    .toLowerCase();
  const aria = normalizeText(hint.ariaLabel).toLowerCase();
  const classes = actionClassTokens(hint.className);
  if (!text.trim() && classes.length === 0) return null;
  const hasToken = (word: string): boolean =>
    classes.some((token) => token.includes(word));
  const voteAnchored = (word: string): boolean =>
    aria.startsWith(word) || aria === word || hasToken(word);
  if (voteAnchored("downvote")) return "dislike";
  if (voteAnchored("upvote")) return "like";
  const hit = (pattern: string): boolean =>
    matchesWord(text, pattern) || matchesWord(classes.join(" "), pattern);
  if (hit("save") || hit("bookmark")) return "favorite";
  // Real count labels are plural ("32 comments") — tolerate the plural form.
  if (hit("comments?") || hit("repl(?:y|ies)")) return "comment";
  if (hit("shares?")) return "share";
  if (hit("join") || hit("follow")) return "follow";
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
  extractSearchQuery: (url) => queryParam(url, "q"),
  cardSelector: CARD_SELECTOR,
  searchInputSelector: SEARCH_INPUT_SELECTOR,
  videoSelector: null,
  dwellPageTypes: ["post"],
  inferActionType: inferRedditActionType,
  buildEventMetadata(url: string): Record<string, unknown> {
    return metadataFromRedditUrl(url);
  },
  buildTargetMetadata: buildRedditTargetMetadata,
};
