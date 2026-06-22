/**
 * Generic behavior kernel — DOM snapshot + BehaviorEvent factory.
 *
 * Platform-specific logic (page-type rules, content-id extraction,
 * action keywords) lives in `shared/platforms/*` and is passed in as
 * a PlatformAdapter.
 */

import type { ActionHint, BehaviorContext, BehaviorEvent, PlatformAdapter } from "./types.js";

const PRIMARY_ACTION_TARGET_SELECTOR = "button,[role='button']";
const FALLBACK_ACTION_TARGET_SELECTOR = "a,[aria-label],[title]";

export interface NormalizedActionSignal {
  type: string;
  metadata: Record<string, unknown>;
}

function normalizeText(value: string | null | undefined): string {
  return (value ?? "").trim();
}

export function normalizeActionSignal(
  actionType: string,
  metadata: Record<string, unknown> = {},
): NormalizedActionSignal {
  if (actionType === "dislike") {
    return {
      type: "feedback",
      metadata: {
        ...metadata,
        feedback_type: "dislike",
        reaction: "thumbs_down",
      },
    };
  }
  return { type: actionType, metadata };
}

function elementClassName(element: Element): string {
  const value = (element as HTMLElement).className;
  return typeof value === "string" ? value : (element.getAttribute("class") ?? "");
}

export function buildActionHintFromClickTarget(target: Element): ActionHint {
  const actionElement =
    target.closest(PRIMARY_ACTION_TARGET_SELECTOR) ??
    target.closest(FALLBACK_ACTION_TARGET_SELECTOR) ??
    target;
  return {
    text: actionElement.textContent,
    ariaLabel:
      actionElement.getAttribute("aria-label") ?? actionElement.getAttribute("title"),
    className: elementClassName(actionElement),
  };
}

export function createDOMSnapshot(doc: Document): string {
  const snapshot: Record<string, string | null> = {
    title: doc.title,
    h1: normalizeText(doc.querySelector("h1")?.textContent),
    description:
      doc.querySelector('meta[name="description"]')?.getAttribute("content")?.trim() ?? null,
    author: normalizeText(
      doc.querySelector(
        ".up-name,.username,.bili-video-card__info--author,.up-info__name,.author-wrapper .username,.author-name",
      )?.textContent,
    ),
  };
  return JSON.stringify(snapshot);
}

export function createBehaviorContext(
  win: Window,
  doc: Document,
  adapter: PlatformAdapter,
  options: { snapshot?: boolean } = {},
): BehaviorContext {
  return {
    pageType: adapter.detectPageType(win.location.href),
    ...(options.snapshot !== false && { domSnapshot: createDOMSnapshot(doc) }),
    viewport: { width: win.innerWidth, height: win.innerHeight },
    scrollPosition: win.scrollY,
  };
}

export function createBehaviorEvent(
  type: string,
  win: Window,
  doc: Document,
  adapter: PlatformAdapter,
  metadata: Record<string, unknown> = {},
  options: { snapshot?: boolean } = {},
): BehaviorEvent {
  const url = win.location.href;
  const contentId = adapter.extractContentId(url);
  const platformMeta = adapter.buildEventMetadata(url);
  return {
    type,
    url,
    title: doc.title,
    timestamp: Date.now(),
    source_platform: adapter.sourcePlatform,
    context: createBehaviorContext(win, doc, adapter, options),
    metadata: {
      ...platformMeta,
      ...(contentId ? { content_id: contentId } : {}),
      ...metadata,
    },
  };
}

export function isTrackableCardElement(
  element: Element | null,
  adapter: PlatformAdapter,
): boolean {
  if (!element) return false;
  return Boolean(element.closest(adapter.cardSelector));
}
