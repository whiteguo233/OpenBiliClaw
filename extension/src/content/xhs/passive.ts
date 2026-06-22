/**
 * xhs passive URL collector — pure helpers.
 *
 * Extracts note URLs from anchors that are already rendered into (or just
 * outside) the viewport as the user browses. The collector never scrolls
 * — it only reacts to the user's own scrolling. Auto-scroll bots are a
 * textbook xhs risk-control signal, so we stay strictly passive.
 *
 * Every helper here is framework-free so tests can feed in minimal
 * anchor-like objects under node --test.
 */

import { pickMetricCount } from "../metric-count.ts";

/** Note detail URL variants xhs exposes. We accept any non-empty segment
 *  after the prefix; backend validation can tighten the id shape. */
const NOTE_PATH_PATTERNS = [/^\/explore\/[^/?#]+/i, /^\/discovery\/item\/[^/?#]+/i];

/** Query params we preserve. xsec_token is required by xhs detail APIs. */
const PRESERVED_QUERY_PARAMS = new Set(["xsec_token"]);

const DEFAULT_TOLERANCE_BELOW_PX = 0;
const DEFAULT_TOLERANCE_ABOVE_PX = 0;

export type XhsPageType = "search" | "profile" | "note" | "explore" | "other";

export interface ViewportRect {
  top: number;
  bottom: number;
  height: number;
}

export interface AnchorLike {
  href: string;
  rect: DOMRect;
}

export interface CollectOptions {
  baseUrl: string;
  /** Extra px below viewport to still count as "visible" (for lazy-loaded rows). */
  toleranceBelowPx?: number;
  /** Extra px above viewport — lets the collector catch cards just scrolled past. */
  toleranceAbovePx?: number;
}

export interface XhsNoteMetadata {
  url: string;
  title: string;
  author: string;
  cover_url: string;
  view_count?: number;
  like_count?: number;
  collect_count?: number;
  comment_count?: number;
}

/**
 * Logged-in user fingerprint extracted from XHS state.
 *
 * v0.3.10+: passed alongside ``XhsUrlObservation`` so the backend
 * (v0.3.57+ ``_extract_self_info_from_payload``) can persist it on
 * first arrival and filter self-authored notes from any ingest path.
 */
export interface XhsSelfInfo {
  user_id: string;
  nickname: string;
}

export interface XhsUrlObservation {
  urls: string[];
  notes: XhsNoteMetadata[];
  page_type: XhsPageType;
  observed_at: number;
  /**
   * Optional logged-in user fingerprint, included whenever the page
   * exposed it via ``__INITIAL_STATE__``. Backend treats absence as
   * "use whatever's already persisted, don't change it".
   */
  self_info?: XhsSelfInfo;
}

export function classifyXhsPageType(url: string): XhsPageType {
  if (url.includes("/search_result")) return "search";
  if (url.includes("/user/profile/")) return "profile";
  if (url.includes("/explore/") || url.includes("/discovery/item/")) return "note";
  if (url.includes("/explore")) return "explore";
  return "other";
}

function matchesNotePath(pathname: string): boolean {
  return NOTE_PATH_PATTERNS.some((pattern) => pattern.test(pathname));
}

export function extractXhsNoteUrl(href: string, baseUrl: string): string | null {
  if (!href || href.startsWith("javascript:") || href.startsWith("mailto:")) {
    return null;
  }

  let parsed: URL;
  try {
    parsed = new URL(href, baseUrl);
  } catch {
    return null;
  }

  if (!matchesNotePath(parsed.pathname)) return null;

  const keptParams = new URLSearchParams();
  parsed.searchParams.forEach((value, key) => {
    if (PRESERVED_QUERY_PARAMS.has(key)) {
      keptParams.set(key, value);
    }
  });

  const query = keptParams.toString();
  return `${parsed.origin}${parsed.pathname}${query ? `?${query}` : ""}`;
}

function isWithinViewport(
  rect: DOMRect,
  viewport: ViewportRect,
  toleranceAbovePx: number,
  toleranceBelowPx: number,
): boolean {
  const upperBound = viewport.bottom + toleranceBelowPx;
  const lowerBound = viewport.top - toleranceAbovePx;
  return rect.bottom >= lowerBound && rect.top <= upperBound;
}

export function collectInViewportNoteUrls(
  anchors: Iterable<AnchorLike>,
  viewport: ViewportRect,
  options: CollectOptions,
): string[] {
  const toleranceBelow = options.toleranceBelowPx ?? DEFAULT_TOLERANCE_BELOW_PX;
  const toleranceAbove = options.toleranceAbovePx ?? DEFAULT_TOLERANCE_ABOVE_PX;

  const ordered: string[] = [];
  const seen = new Set<string>();

  for (const anchor of anchors) {
    if (!isWithinViewport(anchor.rect, viewport, toleranceAbove, toleranceBelow)) {
      continue;
    }
    const url = extractXhsNoteUrl(anchor.href, options.baseUrl);
    if (!url || seen.has(url)) continue;
    seen.add(url);
    ordered.push(url);
  }

  return ordered;
}

/**
 * Extract metadata from a note card's DOM. Best-effort — returns partial
 * data if selectors don't match (xhs DOM changes frequently).
 *
 * The caller passes the ``<a>`` element; we walk up to find the card
 * container, then query inside it for title/author/cover.
 */
export function extractNoteMetadataFromAnchor(
  anchor: HTMLAnchorElement,
  baseUrl: string,
): XhsNoteMetadata | null {
  const url = extractXhsNoteUrl(anchor.href, baseUrl);
  if (!url) return null;

  // Walk up to the card container — xhs uses .note-item or a nearby section/div
  const card =
    anchor.closest(".note-item, section, [class*='note'], [class*='card']") ?? anchor;

  const titleEl = card.querySelector(
    ".title, .note-title, [class*='title'] span, [class*='title']",
  );
  const title = titleEl?.textContent?.trim() || anchor.title || "";

  // Skip notes with empty title — xhs frequently changes DOM structure,
  // so CSS selectors can fail to match.  An empty title produces blank
  // recommendation cards and wastes LLM classification budget.
  if (!title) return null;

  const authorEl = card.querySelector(
    ".author-wrapper .name, .author .name, .user-name, [class*='author'] .name, .nickname",
  );
  const author = authorEl?.textContent?.trim() || "";

  const coverImg = card.querySelector(
    "img.cover, .cover img, img[src*='xhscdn'], img[src*='sns-img'], img",
  );
  const cover_url =
    coverImg?.getAttribute("src") || coverImg?.getAttribute("data-src") || "";

  const view_count = pickMetricCount(card, ["浏览", "观看", "view"]);
  const like_count = pickMetricCount(card, ["赞", "点赞", "喜欢", "like"]);
  const collect_count = pickMetricCount(card, ["收藏", "collect", "save"]);
  const comment_count = pickMetricCount(card, ["评论", "comment"]);

  return {
    url,
    title,
    author,
    cover_url,
    ...(view_count > 0 ? { view_count } : {}),
    ...(like_count > 0 ? { like_count } : {}),
    ...(collect_count > 0 ? { collect_count } : {}),
    ...(comment_count > 0 ? { comment_count } : {}),
  };
}

/**
 * Drop notes whose author matches the logged-in user.
 *
 * v0.3.10+: scrape-time defense in depth — even though the v0.3.57
 * backend filters again on ingest, doing it here reduces network
 * chatter and survives any future protocol drift on the server side.
 *
 * Comparison is on trimmed, case-insensitive ``author`` vs ``nickname``.
 * Returns the input unchanged when ``selfInfo`` is null or has an
 * empty nickname (we never had a fingerprint to compare against).
 */
export function filterSelfAuthoredNotes(
  notes: readonly XhsNoteMetadata[],
  selfInfo: XhsSelfInfo | null | undefined,
): XhsNoteMetadata[] {
  if (!selfInfo) return [...notes];
  const nickname = (selfInfo.nickname || "").trim().toLowerCase();
  if (!nickname) return [...notes];
  return notes.filter(
    (note) => (note.author || "").trim().toLowerCase() !== nickname,
  );
}

/**
 * Remove URLs already present in ``seen`` and record the fresh ones in it.
 *
 * This gives each content-script page-session a monotonic "urls I've
 * already reported" record so we don't re-POST the same batch every time
 * the user scrolls.
 */
export function dedupeObservedUrls(urls: Iterable<string>, seen: Set<string>): string[] {
  const fresh: string[] = [];
  for (const url of urls) {
    if (seen.has(url)) continue;
    seen.add(url);
    fresh.push(url);
  }
  return fresh;
}
