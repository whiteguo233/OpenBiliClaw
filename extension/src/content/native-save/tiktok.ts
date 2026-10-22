/**
 * TikTok native save (favorite) content executor.
 *
 * TikTok's save surface is a single bookmark toggle on the video page's
 * action bar — no playlist dialog (unlike YouTube) and no API replay
 * (contract docs/platform-source-contract.tiktok.toml: the extension
 * never reconstructs engagement POSTs). The flow mirrors douyin.ts:
 * locate the exact correlated control, click it, then require positive
 * checked-state evidence — an unobserved confirmation is
 * native_confirmation_not_observed, never a guessed success
 * (contract terminal_policy).
 *
 * The environment is injectable so unit tests drive a fake DOM-free
 * harness; createTiktokBrowserEnvironment is the real DOM adapter.
 */

import type { NativeSaveTask } from "../../shared/native-save.ts";
import { waitForNativeSaveReadiness } from "./readiness.ts";
import { detectTiktokAccessState } from "../tt/dom-extractor.ts";
import { readTiktokUniversalData, TIKTOK_UNIVERSAL_DATA_ELEMENT_ID } from "../tt/bootstrap.ts";

export interface TiktokSaveControl {
  isSelected(): boolean;
  click(): void;
}

export interface TiktokFavoriteRequestWatch {
  /** Completion only; neither HTTP success nor persistence is inferred. */
  isComplete(): boolean;
  dispose(): void;
}

export interface TiktokNativeSaveEnvironment {
  currentUrl: string;
  isLoggedIn(): boolean;
  /** Explicit login wall/logout evidence, distinct from controls still loading. */
  isLoginRequired?(): boolean;
  isUnavailable(): boolean;
  isContentReady(): boolean;
  rateLimitFingerprint(): string;
  findFavoriteControls(contentId: string): TiktokSaveControl[];
  /** Observe the page's own request before clicking; never send/replay it. */
  watchFavoriteRequest(contentId: string): TiktokFavoriteRequestWatch | null;
  sleep(ms: number): Promise<void>;
}

const CONFIRM_ATTEMPTS = 100;
const CONFIRM_INTERVAL_MS = 100;
const CONTENT_ID_PATTERN = /^[0-9]{6,30}$/;
const TARGET_LABEL = "TikTok Favorites";

/**
 * Exact video identity from a TikTok URL. Strict: https, tiktok.com host,
 * /@<handle>/video/<digits> path, no query or fragment — the same shape
 * the backend's `_native_save_e2e_content_id_from_url` accepts.
 */
export function tiktokRouteVideoId(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.port) return null;
    if (url.hash || url.search) return null;
    if (url.hostname !== "tiktok.com" && !url.hostname.endsWith(".tiktok.com")) return null;
    const match = /^\/@[\w.-]+\/video\/([0-9]{6,30})\/?$/.exec(url.pathname);
    return match?.[1] ?? null;
  } catch {
    return null;
  }
}

function isSupported(task: NativeSaveTask, currentUrl: string): boolean {
  return (
    task.platform === "tiktok" &&
    task.platform_slug === "tiktok" &&
    task.item_key === `tiktok:${task.content_id}` &&
    task.content_type === "video" &&
    CONTENT_ID_PATTERN.test(task.content_id) &&
    tiktokRouteVideoId(task.content_url) === task.content_id &&
    tiktokRouteVideoId(currentUrl) === task.content_id
  );
}

function hasTargetContract(task: NativeSaveTask): boolean {
  return (
    task.target_label === TARGET_LABEL &&
    task.resolved_action === "favorite" &&
    (task.requested_action === "favorite" || task.requested_action === "watch_later")
  );
}

async function confirmSelected(
  task: NativeSaveTask,
  env: TiktokNativeSaveEnvironment,
  request: TiktokFavoriteRequestWatch,
): Promise<boolean> {
  for (let attempt = 0; attempt < CONFIRM_ATTEMPTS; attempt += 1) {
    if (!isSupported(task, env.currentUrl) || !env.isLoggedIn() || env.isUnavailable()) return false;
    const controls = env.findFavoriteControls(task.content_id);
    if (controls.length === 1 && controls[0].isSelected() && request.isComplete()) return true;
    if (attempt + 1 < CONFIRM_ATTEMPTS) await env.sleep(CONFIRM_INTERVAL_MS);
  }
  return false;
}

function hasNewRateLimit(before: string, after: string): boolean {
  const baseline = new Set(before.split("\n").filter(Boolean));
  return after.split("\n").filter(Boolean).some((entry) => !baseline.has(entry));
}

export async function saveTiktok(
  task: NativeSaveTask,
  env: TiktokNativeSaveEnvironment = createTiktokBrowserEnvironment(),
): Promise<unknown> {
  if (!isSupported(task, env.currentUrl) || env.isUnavailable()) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  if (!hasTargetContract(task)) return { status: "failed", error_code: "native_save_failed" };
  const contentReady = await waitForNativeSaveReadiness(
    () => Boolean(env.isLoginRequired?.()) || env.isUnavailable() ||
      (env.isLoggedIn() && env.isContentReady()),
    env.sleep,
    20_000,
  );
  if (!contentReady) return env.isLoggedIn()
    ? { status: "failed", error_code: "native_content_not_ready" }
    : { status: "login_required" };
  // Login evidence must be a positive signed-in marker observed in the
  // page (avatar/nav profile), never cookie presence (contract [auth]).
  if (!env.isLoggedIn()) return { status: "login_required" };
  if (env.isUnavailable()) return { status: "unsupported", error_code: "unsupported_content_type" };
  if (!env.isContentReady()) return { status: "failed", error_code: "native_content_not_ready" };
  if (!isSupported(task, env.currentUrl)) {
    return { status: "failed", error_code: "native_content_changed" };
  }
  const initial = env.findFavoriteControls(task.content_id);
  if (initial.length !== 1) return { status: "failed", error_code: "native_control_not_found" };
  if (initial[0].isSelected()) return { status: "already_synced" };
  const rateLimitBefore = env.rateLimitFingerprint();

  const request = env.watchFavoriteRequest(task.content_id);
  if (!request) return { status: "failed", error_code: "native_save_failed" };
  try {
    initial[0].click();
    if (await confirmSelected(task, env, request)) return { status: "synced" };
  } catch {
    return { status: "failed", error_code: "native_save_failed" };
  } finally {
    request.dispose();
  }
  return hasNewRateLimit(rateLimitBefore, env.rateLimitFingerprint())
    ? { status: "rate_limited" }
    : { status: "failed", error_code: "native_confirmation_not_observed" };
}

/** Read-only membership re-check after reload; never clicks the control. */
export async function verifyTiktok(
  task: NativeSaveTask,
  env: TiktokNativeSaveEnvironment = createTiktokBrowserEnvironment(),
): Promise<unknown> {
  if (!isSupported(task, env.currentUrl) || env.isUnavailable()) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  if (!hasTargetContract(task)) return { status: "failed", error_code: "native_save_failed" };
  const contentReady = await waitForNativeSaveReadiness(
    () => Boolean(env.isLoginRequired?.()) || env.isUnavailable() ||
      (env.isLoggedIn() && env.isContentReady()),
    env.sleep,
    20_000,
  );
  if (!contentReady) return env.isLoggedIn()
    ? { status: "failed", error_code: "native_content_not_ready" }
    : { status: "login_required" };
  if (!env.isLoggedIn()) return { status: "login_required" };
  if (env.isUnavailable()) return { status: "unsupported", error_code: "unsupported_content_type" };
  if (!env.isContentReady()) return { status: "failed", error_code: "native_content_not_ready" };
  if (!isSupported(task, env.currentUrl)) {
    return { status: "failed", error_code: "native_content_changed" };
  }
  const controls = env.findFavoriteControls(task.content_id);
  if (controls.length !== 1) return { status: "failed", error_code: "native_control_not_found" };
  return controls[0].isSelected()
    ? { status: "already_synced" }
    : { status: "failed", error_code: "native_confirmation_not_observed" };
}

// ---------------------------------------------------------------------------
// Real DOM environment
// ---------------------------------------------------------------------------

const FAVORITE_CONTROL_SELECTOR = [
  "[data-e2e='favorite-icon']",
  "[data-e2e='video-favorite']",
  "[data-e2e='browse-video-favorite']",
  "button[aria-label*='Favorite']",
  "[role='button'][aria-label*='Favorite']",
  "button[aria-label*='收藏']",
  "[role='button'][aria-label*='收藏']",
].join(", ");

const UNAVAILABLE_SELECTOR = [
  "[data-e2e='video-unavailable']",
  "[data-e2e='error-page']",
].join(", ");

const RATE_LIMIT_PATTERN =
  /(?:too many requests|try again later|rate limit|操作频繁|请求频繁|稍后再试|429)/i;
const RATE_LIMIT_SELECTOR = "[role='alert'], [data-e2e='toast'], [class*='toast']";

function isEffectivelyVisible(element: HTMLElement, root: Document): boolean {
  const view = root.defaultView ?? element.ownerDocument?.defaultView;
  let current: HTMLElement | null = element;
  while (current) {
    if (
      current.hidden || current.hasAttribute("hidden") || current.hasAttribute("inert") ||
      current.getAttribute("aria-hidden") === "true" || current.style.display === "none" ||
      current.style.visibility === "hidden"
    ) {
      return false;
    }
    if (view) {
      const style = view.getComputedStyle(current);
      if (style.display === "none" || style.visibility === "hidden") return false;
    }
    current = current.parentElement;
  }
  return true;
}

function selected(element: HTMLElement, root: Document): boolean {
  if (
    element.getAttribute("aria-pressed") === "true" ||
    element.getAttribute("aria-checked") === "true" ||
    element.getAttribute("data-e2e-state") === "active"
  ) {
    return true;
  }
  const label = (
    element.getAttribute("aria-label") ?? element.title ?? element.textContent ?? ""
  ).trim();
  if (/(?:Remove from Favorites|取消收藏|已收藏)/i.test(label)) return true;
  // Observed desktop UI (2026-10-12) keeps its "添加到收藏" label after
  // selection, changing only this bookmark glyph to TikTok's gold token.
  // This is optimistic UI evidence: the runner must reload and verify it.
  if (element.getAttribute("data-e2e") !== "favorite-icon") return false;
  const icons = element.querySelectorAll<SVGElement>('span[class*="--SpanBookmarkWrapper"] svg');
  return icons.length === 1 && root.defaultView?.getComputedStyle(icons[0]).color === "rgb(250, 206, 21)";
}

function isWithinViewport(element: HTMLElement, root: Document): boolean {
  const view = root.defaultView;
  if (!view) return false;
  const rect = element.getBoundingClientRect();
  // TikTok preloads the next article below the viewport with display/visibility
  // still enabled. It must never become a candidate for the current URL.
  return rect.width > 0 && rect.height > 0 && rect.left >= 0 && rect.top >= 0 &&
    rect.right <= view.innerWidth && rect.bottom <= view.innerHeight;
}

function favoriteControls(root: Document, currentUrl: string, contentId: string): HTMLElement[] {
  if (tiktokRouteVideoId(currentUrl) !== contentId) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(FAVORITE_CONTROL_SELECTOR)).filter(
    (element) => isEffectivelyVisible(element, root) && isWithinViewport(element, root),
  );
}

/** Passive timing observation avoids reloading away an optimistic in-flight POST. */
function watchFavoriteRequest(root: Document, currentUrl: string, contentId: string): TiktokFavoriteRequestWatch | null {
  const view = root.defaultView;
  if (!view?.PerformanceObserver || tiktokRouteVideoId(currentUrl) !== contentId) return null;
  const startedAt = view.performance.now();
  const origin = new URL(currentUrl).origin;
  let complete = false;
  const inspect = (entries: PerformanceEntry[]): void => {
    for (const entry of entries) {
      const timing = entry as PerformanceResourceTiming;
      if (entry.entryType !== "resource" || entry.startTime < startedAt || timing.responseEnd <= 0 ||
          (timing.initiatorType !== "fetch" && timing.initiatorType !== "xmlhttprequest")) continue;
      try {
        const url = new URL(entry.name);
        if (url.origin === origin && url.pathname === "/api/item/collect/" &&
            url.searchParams.get("action") === "1" && url.searchParams.get("itemId") === contentId) {
          complete = true;
        }
      } catch { /* Ignore unrelated resource names. */ }
    }
  };
  const observer = new view.PerformanceObserver(list => inspect(list.getEntries()));
  try {
    // No buffered history: only requests started after this attempt count.
    // Unlike getEntriesByType, observing also works when the resource buffer is full.
    observer.observe({ type: "resource", buffered: false });
  } catch {
    observer.disconnect();
    return null;
  }
  return {
    isComplete() {
      inspect(observer.takeRecords());
      return complete;
    },
    dispose() { observer.disconnect(); },
  };
}

export function createTiktokBrowserEnvironment(
  root: Document = document,
  currentUrl?: string,
): TiktokNativeSaveEnvironment {
  const readUrl = (): string => currentUrl ?? root.location?.href ?? location.href;
  const rateElementIds = new WeakMap<Element, number>();
  let nextRateElementId = 1;
  return {
    get currentUrl() { return readUrl(); },
    isLoggedIn() {
      const state = detectTiktokAccessState(root, readUrl());
      const identity = readTiktokUniversalData(
        root.getElementById(TIKTOK_UNIVERSAL_DATA_ELEMENT_ID)?.textContent ?? "",
      );
      return state !== "login_wall" && !identity.explicitLoggedOut &&
        (state === "signed_in" || identity.loggedIn);
    },
    isLoginRequired() {
      return detectTiktokAccessState(root, readUrl()) === "login_wall" ||
        readTiktokUniversalData(root.getElementById(TIKTOK_UNIVERSAL_DATA_ELEMENT_ID)?.textContent ?? "").explicitLoggedOut;
    },
    isUnavailable() {
      return Array.from(root.querySelectorAll<HTMLElement>(UNAVAILABLE_SELECTOR)).some(
        (element) => isEffectivelyVisible(element, root),
      );
    },
    isContentReady() {
      const contentId = tiktokRouteVideoId(readUrl());
      return contentId !== null && favoriteControls(root, readUrl(), contentId).length > 0;
    },
    rateLimitFingerprint() {
      return Array.from(root.querySelectorAll<HTMLElement>(RATE_LIMIT_SELECTOR))
        .filter((element) => {
          const text = element.textContent?.trim() ?? "";
          return RATE_LIMIT_PATTERN.test(text) && isEffectivelyVisible(element, root);
        })
        .map((element) => {
          let id = rateElementIds.get(element);
          if (id === undefined) {
            id = nextRateElementId;
            nextRateElementId += 1;
            rateElementIds.set(element, id);
          }
          return `${id}:${element.textContent?.trim() ?? ""}`;
        })
        .join("\n");
    },
    findFavoriteControls(contentId) {
      return favoriteControls(root, readUrl(), contentId).flatMap((element) => {
        // The current desktop favorite-icon is a wrapper around a native
        // button. Clicking the wrapper does not activate that inner action.
        const buttons = Array.from(element.querySelectorAll<HTMLButtonElement>("button"))
          .filter((button) => isEffectivelyVisible(button, root) && isWithinViewport(button, root));
        if (buttons.length > 1) return [];
        const action = buttons[0] ?? element;
        return [{
          isSelected: () => selected(element, root) || selected(action, root),
          click: () => action.click(),
        }];
      });
    },
    watchFavoriteRequest(contentId) {
      return watchFavoriteRequest(root, readUrl(), contentId);
    },
    sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    },
  };
}
