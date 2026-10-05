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

/**
 * Shared action-label guards for the platform adapters (#205).
 *
 * Click hints carry three surfaces: `textContent`, `aria-label`/`title`, and
 * `className`. Video cards and containers put full copy into the first two
 * and full video titles into the second, so keyword substring matching over
 * unguarded surfaces turns ordinary card clicks into like/dislike signals.
 * Every real control label on the Chinese platforms is tiny (不感兴趣 / 不喜欢 /
 * 减少此类推荐 / 反对 all ≤ 6 chars), which is what ACTION_LABEL_MAX_CHARS is
 * calibrated against. English-language platforms (YouTube, Reddit) use longer
 * sentence-style aria-labels — use {@link matchesWord} there instead.
 */
export const ACTION_LABEL_MAX_CHARS = 8;

export function shortActionLabel(value: string | null | undefined, maxChars: number = ACTION_LABEL_MAX_CHARS): string {
  const text = normalizeText(value);
  return text.length <= maxChars ? text : "";
}

/** Lowercased class tokens; prevents one long class string matching a keyword
 *  as an incidental substring. */
export function actionClassTokens(className: string): string[] {
  return className.toLowerCase().split(/\s+/).filter(Boolean);
}

/** Whole-word match for English keywords ("like" must not hit "unlikely"). */
export function matchesWord(text: string, word: string): boolean {
  return new RegExp(`\\b${word}\\b`).test(text);
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

/**
 * Whether the generic DOM click path should suppress emitting a strong-signal
 * action because the platform's MAIN-world network tap is the authoritative
 * source for it. `action` is the `inferActionType` output for a positive
 * signal, or the literal `"retraction"` for a pressed-control withdrawal.
 *
 * Per-action granularity (replaces the old coarse `strongSignalSource` flag):
 * a platform can let its tap own like/favorite while still emitting other
 * DOM actions, so we never double-count nor fire "opened the menu" false
 * actions for tap-owned actions.
 */
export function isTapAuthoritativeAction(
  adapter: Pick<PlatformAdapter, "tapAuthoritativeActions">,
  action: string,
): boolean {
  return adapter.tapAuthoritativeActions?.has(action) ?? false;
}

function elementClassName(element: Element): string {
  const value = (element as HTMLElement).className;
  return typeof value === "string" ? value : (element.getAttribute("class") ?? "");
}

function readPressedState(element: Element): boolean | null {
  const raw = element.getAttribute("aria-pressed");
  if (raw === "true") return true;
  if (raw === "false") return false;
  // Absent or any other value ("mixed", etc.) → unknown; fail open.
  return null;
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
    pressed: readPressedState(actionElement),
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
    event_id: globalThis.crypto.randomUUID(),
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
