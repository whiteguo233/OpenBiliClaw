import type { ActionHint, BehaviorContext, BehaviorEvent, PageType } from "./types.js";

const BV_PATTERN = /(BV[0-9A-Za-z]{10})/;

function normalizeText(value: string | null | undefined): string {
  return (value ?? "").trim();
}

export function detectPageType(url: string): PageType {
  if (url.includes("/video/")) return "video";
  if (url.includes("/search")) return "search";
  if (url.includes("space.bilibili.com") || url.includes("/space/")) return "user";
  if (url.includes("/v/")) return "category";
  return "home";
}

export function extractBvid(url: string): string | null {
  return url.match(BV_PATTERN)?.[1] ?? null;
}

export function createDOMSnapshot(doc: Document): string {
  const snapshot: Record<string, string | null> = {
    title: doc.title,
    h1: normalizeText(doc.querySelector("h1")?.textContent),
    description:
      doc.querySelector('meta[name="description"]')?.getAttribute("content")?.trim() ?? null,
    author: normalizeText(
      doc.querySelector(".up-name,.username,.bili-video-card__info--author,.up-info__name")
        ?.textContent,
    ),
  };
  return JSON.stringify(snapshot);
}

export function createBehaviorContext(win: Window, doc: Document): BehaviorContext {
  return {
    pageType: detectPageType(win.location.href),
    domSnapshot: createDOMSnapshot(doc),
    viewport: { width: win.innerWidth, height: win.innerHeight },
    scrollPosition: win.scrollY,
  };
}

export function createBehaviorEvent(
  type: string,
  win: Window,
  doc: Document,
  metadata: Record<string, unknown> = {},
): BehaviorEvent {
  return {
    type,
    url: win.location.href,
    title: doc.title,
    timestamp: Date.now(),
    context: createBehaviorContext(win, doc),
    metadata,
  };
}

export function inferActionType(hint: ActionHint): string | null {
  const text = `${normalizeText(hint.text)} ${normalizeText(hint.ariaLabel)} ${hint.className}`
    .toLowerCase();

  if (!text) return null;
  if (text.includes("点赞") || text.includes("like")) return "like";
  if (text.includes("投币") || text.includes("coin")) return "coin";
  if (text.includes("收藏") || text.includes("collect") || text.includes("favorite")) {
    return "favorite";
  }
  if (text.includes("评论") || text.includes("comment")) return "comment";
  return null;
}

export function isTrackableCardElement(element: Element | null): boolean {
  if (!element) return false;
  return Boolean(
    element.closest(
      [
        'a[href*="/video/BV"]',
        ".bili-video-card",
        ".video-page-card",
        ".search-all-list .video-item",
        ".feed-card",
      ].join(","),
    ),
  );
}
