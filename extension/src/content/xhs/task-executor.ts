/**
 * xhs task executor — content-script side.
 *
 * When the background dispatcher opens a tab with a search or creator
 * page, this module extracts up to 20 notes and emits `XHS_TASK_RESULT`
 * back to the service worker. Search tasks prefer normalized page API
 * responses so hidden tabs do not depend on the virtualized DOM mounting;
 * rendered cards remain the fallback for schema drift and creator pages.
 *
 * Bootstrap profile imports can optionally scroll when the backend requests
 * it. Search/creator discovery remains passive: it never scrolls or mutates
 * the page, and search only observes the page's own response.
 */

import { attachCoverData, backfillCoverUrlsFromState } from "./cover-harvest.js";
import {
  collectInViewportNoteUrls,
  extractNoteMetadataFromAnchor,
  filterSelfAuthoredNotes,
  type AnchorLike,
  type ViewportRect,
  type XhsNoteMetadata,
  type XhsSelfInfo,
} from "./passive.js";
import {
  buildBootstrapDebugPayload,
  buildBootstrapPartialPayload,
  bootstrapScrollShouldContinue,
  bootstrapProfileTabLabels,
  clickOwnProfileAnchorFromDocument,
  collectBootstrapScrollCandidates,
  countBootstrapStateNotesByScope,
  extractBootstrapNotesFromProfileDocument,
  extractBootstrapNotesFromState,
  extractBootstrapStateFromDocument,
  extractOwnProfileUrlFromDocument,
  extractOwnProfileUrlFromState,
  extractSelfInfoFromState,
  findBootstrapScrollContainer,
  hasDifferentProfileDocumentNotes,
  hasBootstrapProfileContent,
  isActiveBootstrapProfileTab,
  limitBootstrapNewNotesToRemainingCapacity,
  mergeBootstrapNotes,
  normalizeBootstrapScrollRounds,
  normalizeBootstrapScrollWaitMs,
  normalizeBootstrapScopes,
  normalizeBootstrapStagnantScrollRounds,
  profileDocumentNoteKeys,
  readBootstrapScrollMetrics,
  type BootstrapScrollMetrics,
  type XhsBootstrapNote,
  type XhsBootstrapScope,
} from "./bootstrap.js";
import {
  detectXhsTaskLoginRequired,
  detectXhsTaskRiskControl,
  type XhsRiskControlDetection,
} from "./risk-control.js";
import { NOTE_ANCHOR_SELECTOR } from "./selectors.ts";
import {
  readXhsSearchResponseNotes,
  requestXhsSearchResponseReplay,
} from "./search-response-buffer.js";
import { readXhsPublishedTimes } from "./published-time-buffer.js";
import { enrichNotesWithPublishedAt } from "./published-at-enrich.js";
import { mergePublishedTimes } from "../../shared/xhs-published-at.js";

const MAX_URLS = 20;
// Remote background-tab traces on 2026-08-03 showed the old 5s ceiling could
// expire before the SPA mounted its cards. 12s remains comfortably below the
// dispatcher's 30s task timeout while avoiding extra page loads or scrolling.
const RENDER_WAIT_MS = 12_000;
const CHECK_INTERVAL_MS = 300;
const PROFILE_CLICK_DELAY_MS = 150;
const PROFILE_CONTENT_WAIT_MS = 8_000;
// Exact publish times are only required when the source has a non-"all" date
// preference (backend flag). Cards expose no time, so search tasks ask the
// MAIN-world bridge to load at most five note pages; runs concurrently under a
// 6s per-page timeout.
const PUBLISHED_AT_ENRICH_MAX_NOTES = 5;
const PUBLISHED_AT_ENRICH_TIMEOUT_MS = 6_000;

export interface TaskExecuteMessage {
  task_id: string;
  type: "search" | "creator" | "bootstrap_profile";
  scopes?: XhsBootstrapScope[];
  max_items_per_scope?: number;
  max_scroll_rounds?: number;
  scroll_wait_ms?: number;
  max_stagnant_scroll_rounds?: number;
  /**
   * Backend sets this when `[sources.xiaohongshu].recommendation_date_preset`
   * is not "all". Cards expose no time field, so the executor then fetches up
   * to five note-detail pages to fill exact `published_at` (epoch ms).
   */
  need_published_at?: boolean;
}

export interface TaskResultPayload {
  task_id: string;
  urls: string[];
  notes: Array<XhsNoteMetadata | XhsBootstrapNote>;
  scope_counts?: Record<string, number>;
  status: "ok" | "empty" | "partial" | "error" | "rate_limited";
  error?: string;
  next_url?: string;
  debug?: Record<string, unknown>;
  /**
   * v0.3.10+: logged-in user fingerprint, captured from any task type
   * (search / creator / bootstrap_profile). Backend v0.3.57+ reads it
   * via ``_extract_self_info_from_payload`` from the top level.
   * Bootstrap_profile additionally embeds it inside
   * ``debug.xhs_bootstrap.steps[*].self_info`` for backwards compat.
   */
  self_info?: XhsSelfInfo;
}

interface ProfileTabLoadResult {
  notes: XhsBootstrapNote[];
  changed: boolean;
  active: boolean;
  before_count: number;
  after_count: number;
  scroll_rounds: number;
  stagnant_rounds: number;
  scroll_metrics?: ProfileScrollRoundDebug[];
}

type ProfileTabDebugResult = Omit<ProfileTabLoadResult, "notes">;

interface ProfileScrollRoundDebug extends BootstrapScrollMetrics {
  round: number;
  before_top: number;
  after_top: number;
  added_count: number;
  total_count: number;
}

// ---------------------------------------------------------------------------
// Pure helpers (testable)
// ---------------------------------------------------------------------------

export function snapshotAllAnchors(root: Document): AnchorLike[] {
  const nodes = root.querySelectorAll<HTMLAnchorElement>(NOTE_ANCHOR_SELECTOR);
  const out: AnchorLike[] = [];
  nodes.forEach((node) => {
    out.push({ href: node.href, rect: node.getBoundingClientRect() });
  });
  return out;
}

export function buildLargeViewport(win: Window): ViewportRect {
  // Use a very tall viewport so we capture cards beyond the fold too —
  // the page just loaded so everything rendered is fair game.
  const height = win.innerHeight || 900;
  return { top: -500, bottom: height + 500, height: height + 1000 };
}

function safeSelectorCount(doc: Document, selector: string): number {
  try {
    return doc.querySelectorAll(selector).length;
  } catch {
    return 0;
  }
}

function pathnameOnly(rawUrl: string): string {
  try {
    return new URL(rawUrl).pathname;
  } catch {
    return "";
  }
}

/**
 * Build an intentionally content-free empty-page diagnostic.
 *
 * Counts and lifecycle flags are enough to distinguish selector drift,
 * background-tab throttling, and a genuinely empty result. Never include the
 * search keyword, title, body text, href values, cookie, or page state.
 */
function emptySearchTaskResult(
  msg: TaskExecuteMessage,
  win: Window,
  doc: Document,
  reason: "no_note_anchor_after_wait" | "no_urls_in_viewport",
): TaskResultPayload {
  const state = extractBootstrapStateFromDocument(doc);
  return {
    task_id: msg.task_id,
    urls: [],
    notes: [],
    status: "empty",
    error: "xhs_empty_result",
    debug: {
      xhs_search_empty: {
        reason,
        task_type: msg.type,
        pathname: pathnameOnly(win.location.href),
        ready_state: doc.readyState,
        visibility_state: doc.visibilityState,
        hidden: doc.hidden,
        viewport_width: Math.max(0, Math.floor(win.innerWidth || 0)),
        viewport_height: Math.max(0, Math.floor(win.innerHeight || 0)),
        body_child_count: doc.body?.childElementCount ?? 0,
        state_available: state !== null,
        anchor_counts: {
          all: safeSelectorCount(doc, "a[href]"),
          note: safeSelectorCount(doc, NOTE_ANCHOR_SELECTOR),
          explore: safeSelectorCount(doc, 'a[href*="/explore/"]'),
          discovery_item: safeSelectorCount(doc, 'a[href*="/discovery/item/"]'),
          search_result: safeSelectorCount(doc, 'a[href*="/search_result/"]'),
        },
      },
    },
  };
}

function loginRequiredTaskResult(
  msg: TaskExecuteMessage,
  win: Window,
): TaskResultPayload {
  return {
    task_id: msg.task_id,
    urls: [],
    notes: [],
    status: "error",
    error: "xhs_login_required",
    debug: {
      xhs_auth: {
        reason: "visible_login_overlay",
        task_type: msg.type,
        pathname: pathnameOnly(win.location.href),
      },
    },
  };
}

// ---------------------------------------------------------------------------
// Chrome integration
// ---------------------------------------------------------------------------

function waitForCards(doc: Document): Promise<boolean> {
  return new Promise((resolve) => {
    // Quick check — cards may already be present.
    if (doc.querySelectorAll(NOTE_ANCHOR_SELECTOR).length > 0) {
      resolve(true);
      return;
    }

    let settled = false;
    const observer = new MutationObserver(() => {
      if (doc.querySelectorAll(NOTE_ANCHOR_SELECTOR).length > 0) {
        settled = true;
        observer.disconnect();
        resolve(true);
      }
    });
    observer.observe(doc.body ?? doc.documentElement, {
      childList: true,
      subtree: true,
    });

    // Fallback polling for frameworks that batch mutations.
    const interval = setInterval(() => {
      if (settled) {
        clearInterval(interval);
        return;
      }
      if (doc.querySelectorAll(NOTE_ANCHOR_SELECTOR).length > 0) {
        settled = true;
        observer.disconnect();
        clearInterval(interval);
        resolve(true);
      }
    }, CHECK_INTERVAL_MS);

    // Hard cap — give up after RENDER_WAIT_MS.
    setTimeout(() => {
      if (!settled) {
        settled = true;
        observer.disconnect();
        clearInterval(interval);
        resolve(doc.querySelectorAll(NOTE_ANCHOR_SELECTOR).length > 0);
      }
    }, RENDER_WAIT_MS);
  });
}

async function waitForSearchResponseOrCards(
  doc: Document,
  win: Window,
): Promise<"response" | "cards" | "empty"> {
  requestXhsSearchResponseReplay(win);
  const deadline = Date.now() + RENDER_WAIT_MS;
  while (Date.now() < deadline) {
    if (readXhsSearchResponseNotes().length > 0) return "response";
    if (doc.querySelectorAll(NOTE_ANCHOR_SELECTOR).length > 0) return "cards";
    await sleep(CHECK_INTERVAL_MS);
  }
  return readXhsSearchResponseNotes().length > 0
    ? "response"
    : doc.querySelectorAll(NOTE_ANCHOR_SELECTOR).length > 0
      ? "cards"
      : "empty";
}

function waitForBootstrapProfileContent(doc: Document): Promise<boolean> {
  return new Promise((resolve) => {
    if (hasBootstrapProfileContent(doc)) {
      resolve(true);
      return;
    }

    let settled = false;
    let observer: MutationObserver | null = null;
    let interval: ReturnType<typeof setInterval> | null = null;
    const finish = (ready: boolean) => {
      if (settled) return;
      settled = true;
      observer?.disconnect();
      if (interval !== null) clearInterval(interval);
      resolve(ready);
    };

    try {
      observer = new MutationObserver(() => {
        if (hasBootstrapProfileContent(doc)) finish(true);
      });
      observer.observe(doc.body ?? doc.documentElement, {
        childList: true,
        subtree: true,
        characterData: true,
      });
    } catch {
      observer = null;
    }

    interval = setInterval(() => {
      if (hasBootstrapProfileContent(doc)) finish(true);
    }, CHECK_INTERVAL_MS);

    setTimeout(() => {
      finish(hasBootstrapProfileContent(doc));
    }, PROFILE_CONTENT_WAIT_MS);
  });
}

function isProfilePage(url: string): boolean {
  try {
    return new URL(url).pathname.startsWith("/user/profile/");
  } catch {
    return false;
  }
}

function buildScopeCounts(
  scopes: readonly XhsBootstrapScope[],
  notes: readonly XhsBootstrapNote[] = [],
): Record<string, number> {
  const scope_counts: Record<string, number> = {};
  for (const scope of scopes) {
    scope_counts[scope] = notes.filter((note) => note.scope === scope).length;
  }
  return scope_counts;
}

function buildEmptyStateCounts(scopes: readonly XhsBootstrapScope[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const scope of scopes) counts[scope] = 0;
  return counts;
}

function scheduleOwnProfileNavigationClick(doc: Document, win: Window, baseUrl: string): boolean {
  const profileUrl = extractOwnProfileUrlFromDocument(doc, baseUrl);
  if (!profileUrl) return false;
  win.setTimeout(() => {
    clickOwnProfileAnchorFromDocument(doc, baseUrl, win);
  }, PROFILE_CLICK_DELAY_MS);
  return true;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function rateLimitedTaskResult(
  taskId: string,
  detection: XhsRiskControlDetection,
): TaskResultPayload {
  return {
    task_id: taskId,
    urls: [],
    notes: [],
    status: "rate_limited",
    error: detection.error,
    debug: {
      xhs_risk_control: {
        reason: detection.reason,
      },
    },
  };
}

async function sendTaskResult(result: TaskResultPayload): Promise<void> {
  try {
    await chrome.runtime.sendMessage({
      action: "XHS_TASK_RESULT",
      data: result,
    });
  } catch {
    // If the service worker disappears mid-task, keep the page-side scrape
    // moving so the final result still has a chance to land.
  }
}

function profileTabSelector(): string {
  return [
    "[role='tab']",
    ".tab-item",
    ".reds-tab-item",
    "[class*='tab-item']",
    "[class*='TabItem']",
    "[class*='tabs'] button",
    "[class*='tabs'] a",
    "[class*='Tabs'] button",
    "[class*='Tabs'] a",
    "button",
    "a",
  ].join(", ");
}

function normalizedElementText(candidate: HTMLElement): string {
  return candidate.textContent?.replace(/\s+/g, "").trim() ?? "";
}

function isProfileTabLikeElement(candidate: HTMLElement): boolean {
  const className = String(candidate.className ?? "").toLowerCase();
  if (candidate.getAttribute("role") === "tab") return true;
  if (className.includes("tab")) return true;
  return (
    candidate.closest(
      "[role='tablist'], .reds-tabs-list, .tabs, [class*='tab-list'], [class*='TabList'], [class*='tabs'], [class*='Tabs']",
    ) !== null
  );
}

function tabTextMatches(text: string, labels: readonly string[]): boolean {
  if (!text || text.length > 16) return false;
  return labels.some((label) => text === label || text.startsWith(label));
}

function activateProfileTab(tab: HTMLElement, win: Window): void {
  tab.scrollIntoView({ block: "center", inline: "center" });
  tab.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: win }));
  tab.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: win }));
  tab.click();
}

function extractProfilePageNotes(
  doc: Document,
  scope: XhsBootstrapScope,
  baseUrl: string,
  maxItemsPerScope: number,
): XhsBootstrapNote[] {
  const state = extractBootstrapStateFromDocument(doc);
  const stateNotes = state
    ? extractBootstrapNotesFromState(state, [scope], { baseUrl, maxItemsPerScope })
    : [];
  const domNotes = extractBootstrapNotesFromProfileDocument(doc, scope, baseUrl, {
    maxItemsPerScope,
  });
  return mergeBootstrapNotes([...stateNotes, ...domNotes], [scope], { maxItemsPerScope });
}

function documentScrollMetrics(win: Window, doc: Document): BootstrapScrollMetrics {
  const scrolling = doc.scrollingElement ?? doc.documentElement;
  return {
    target: "document",
    scroll_top: Math.max(0, Math.floor(Math.max(scrolling.scrollTop, win.scrollY || 0))),
    scroll_height: Math.max(0, Math.floor(scrolling.scrollHeight || 0)),
    client_height: Math.max(0, Math.floor(scrolling.clientHeight || win.innerHeight || 0)),
  };
}

function dispatchWheelLikeScroll(win: Window, target: EventTarget, deltaY: number): void {
  try {
    target.dispatchEvent(
      new WheelEvent("wheel", {
        bubbles: true,
        cancelable: true,
        deltaY,
        deltaMode: 0,
        clientX: Math.floor((win.innerWidth || 1200) / 2),
        clientY: Math.floor((win.innerHeight || 900) * 0.75),
      }),
    );
  } catch {
    try {
      target.dispatchEvent(new Event("wheel", { bubbles: true, cancelable: true }));
    } catch {
      // Ignore synthetic event failures; direct scroll calls below are fallback.
    }
  }
}

function scrollProfilePage(win: Window, doc: Document): Omit<ProfileScrollRoundDebug, "round" | "added_count" | "total_count"> {
  const scrollContainer = findBootstrapScrollContainer(doc);
  const scrolling = scrollContainer ?? (doc.scrollingElement as HTMLElement | null) ?? doc.documentElement;
  const before = scrollContainer
    ? readBootstrapScrollMetrics(scrollContainer)
    : documentScrollMetrics(win, doc);
  const currentTop = before.scroll_top;
  const viewportHeight = win.innerHeight || 900;
  const step = Math.max(Math.floor(viewportHeight * 0.8), 640);
  const clientHeight = before.client_height || viewportHeight;
  const nextTop = Math.min(currentTop + step, Math.max(before.scroll_height - clientHeight, 0));
  const wheelTarget = scrollContainer ?? doc.body ?? doc.documentElement;
  const wheelSteps = scrollContainer ? [step] : [220, 260, 240, 280];

  for (const deltaY of wheelSteps) {
    dispatchWheelLikeScroll(win, wheelTarget, deltaY);
    dispatchWheelLikeScroll(win, doc, deltaY);
    dispatchWheelLikeScroll(win, win, deltaY);
  }

  scrolling.scrollTop = nextTop;
  if (!scrollContainer) {
    for (const deltaY of wheelSteps) {
      win.scrollBy({ top: deltaY, behavior: "auto" });
    }
    win.scrollTo({ top: Math.max(nextTop, win.scrollY || 0), behavior: "auto" });
  }
  if (scrollContainer) {
    scrollContainer.dispatchEvent(new Event("scroll", { bubbles: true }));
  }
  win.dispatchEvent(new Event("scroll"));
  const after = scrollContainer
    ? readBootstrapScrollMetrics(scrollContainer)
    : documentScrollMetrics(win, doc);
  return {
    target: after.target,
    scroll_top: after.scroll_top,
    scroll_height: after.scroll_height,
    client_height: after.client_height,
    before_top: before.scroll_top,
    after_top: after.scroll_top,
  };
}

function findProfileTab(doc: Document, labels: readonly string[]): HTMLElement | null {
  const candidates = Array.from(doc.querySelectorAll<HTMLElement>(profileTabSelector()));
  for (const candidate of candidates) {
    const text = normalizedElementText(candidate);
    if (tabTextMatches(text, labels) && isProfileTabLikeElement(candidate)) {
      return candidate;
    }
  }
  return null;
}

/**
 * Poll for the profile sub-tab with the given labels to appear.
 *
 * v0.3.13+: post-2025 Xiaohongshu nests the saved / liked sub-tabs
 * inside the 笔记 outer tab. The sub-tab DIVs render a beat after the
 * profile-page initial paint — calling ``findProfileTab`` on first
 * profile-page load returns ``null`` and the bootstrap_profile task
 * silently gives up on saved / liked. Waiting up to ~5s lets the Vue
 * runtime mount the sub-tabs and then activate them properly.
 */
async function findProfileTabWithRetry(
  doc: Document,
  labels: readonly string[],
  timeoutMs: number = 5_000,
): Promise<HTMLElement | null> {
  const deadline = Date.now() + Math.max(0, timeoutMs);
  // First try is synchronous so existing fast-path stays fast.
  const immediate = findProfileTab(doc, labels);
  if (immediate) return immediate;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, CHECK_INTERVAL_MS));
    const found = findProfileTab(doc, labels);
    if (found) return found;
  }
  return null;
}

function collectProfileTabCandidateTexts(doc: Document): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const candidates = Array.from(doc.querySelectorAll<HTMLElement>(profileTabSelector()));
  for (const candidate of candidates) {
    if (!isProfileTabLikeElement(candidate)) continue;
    const text = normalizedElementText(candidate);
    if (!text || text.length > 16 || seen.has(text)) continue;
    seen.add(text);
    out.push(text);
  }
  return out.slice(0, 12);
}

async function waitForScopeContent(
  doc: Document,
  scope: XhsBootstrapScope,
  tab: HTMLElement,
  baseUrl: string,
  previousKeys: readonly string[],
  maxItemsPerScope: number,
  timeoutMs: number = 5_000,
): Promise<ProfileTabLoadResult> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const notes = extractProfilePageNotes(doc, scope, baseUrl, maxItemsPerScope);
    const changed = hasDifferentProfileDocumentNotes(notes, previousKeys);
    const active = isActiveBootstrapProfileTab(tab);
    if (changed || (active && notes.length > 0)) {
      return {
        notes,
        changed,
        active,
        before_count: previousKeys.length,
        after_count: notes.length,
        scroll_rounds: 0,
        stagnant_rounds: 0,
      };
    }
    await sleep(250);
  }
  const notes = extractProfilePageNotes(doc, scope, baseUrl, maxItemsPerScope);
  return {
    notes: [],
    changed: false,
    active: isActiveBootstrapProfileTab(tab),
    before_count: previousKeys.length,
    after_count: notes.length,
    scroll_rounds: 0,
    stagnant_rounds: 0,
  };
}

async function scrollForMoreProfileNotes(
  taskId: string,
  doc: Document,
  win: Window,
  scope: XhsBootstrapScope,
  baseUrl: string,
  initialNotes: readonly XhsBootstrapNote[],
  maxItemsPerScope: number,
  maxScrollRounds: number,
  scrollWaitMs: number,
  maxStagnantScrollRounds: number,
): Promise<{
  notes: XhsBootstrapNote[];
  scrollRounds: number;
  stagnantRounds: number;
  scrollMetrics: ProfileScrollRoundDebug[];
}> {
  let notes = mergeBootstrapNotes(initialNotes, [scope], { maxItemsPerScope });
  let stagnantRounds = 0;
  let round = 0;
  const scrollMetrics: ProfileScrollRoundDebug[] = [];

  while (
    bootstrapScrollShouldContinue({
      currentCount: notes.length,
      maxItemsPerScope,
      round,
      maxScrollRounds,
      stagnantRounds,
      maxStagnantScrollRounds,
    })
  ) {
    const beforeCount = notes.length;
    const scrollRound = scrollProfilePage(win, doc);
    await sleep(scrollWaitMs);
    const nextNotes = extractProfilePageNotes(doc, scope, baseUrl, maxItemsPerScope);
    const previousKeys = new Set(notes.map((note) => note.note_id || note.url || note.title));
    const newlyAddedCandidates = nextNotes.filter(
      (note) => !previousKeys.has(note.note_id || note.url || note.title),
    );
    const newlyAdded = limitBootstrapNewNotesToRemainingCapacity(
      notes,
      newlyAddedCandidates,
      maxItemsPerScope,
    );
    notes = mergeBootstrapNotes([...notes, ...nextNotes], [scope], { maxItemsPerScope });
    if (newlyAdded.length > 0) {
      await sendTaskResult(
        buildBootstrapPartialPayload({
          taskId,
          scope,
          notes: newlyAdded,
          scopeCounts: { [scope]: notes.length },
          round: round + 1,
        }),
      );
    }
    scrollMetrics.push({
      ...scrollRound,
      round: round + 1,
      added_count: newlyAdded.length,
      total_count: notes.length,
    });
    stagnantRounds = notes.length > beforeCount ? 0 : stagnantRounds + 1;
    round += 1;
  }

  return { notes, scrollRounds: round, stagnantRounds, scrollMetrics };
}

async function loadProfileTabsForScopes(
  taskId: string,
  scopes: readonly XhsBootstrapScope[],
  doc: Document,
  win: Window,
  baseUrl: string,
  maxItemsPerScope: number,
  maxScrollRounds: number,
  scrollWaitMs: number,
  maxStagnantScrollRounds: number,
): Promise<{ notes: XhsBootstrapNote[]; tabResults: Record<string, ProfileTabDebugResult> }> {
  const domNotes: XhsBootstrapNote[] = [];
  const tabResults: Record<string, ProfileTabDebugResult> = {};

  for (const scope of scopes) {
    const labels = bootstrapProfileTabLabels(scope);
    if (labels.length === 0) continue;
    const state = extractBootstrapStateFromDocument(doc);
    if (state) {
      const current = extractBootstrapNotesFromState(state, [scope], { maxItemsPerScope });
      if (current.length > 0) continue;
    }
    const tab = await findProfileTabWithRetry(doc, labels);
    if (!tab) continue;
    const previousKeys = profileDocumentNoteKeys(doc, baseUrl);
    activateProfileTab(tab, win);
    const result = await waitForScopeContent(
      doc,
      scope,
      tab,
      baseUrl,
      previousKeys,
      maxItemsPerScope,
    );
    if (result.notes.length > 0 && maxScrollRounds > 0) {
      await sendTaskResult(
        buildBootstrapPartialPayload({
          taskId,
          scope,
          notes: result.notes,
          scopeCounts: { [scope]: result.notes.length },
          round: 0,
        }),
      );
    }
    const scrolled =
      result.notes.length > 0 && maxScrollRounds > 0
        ? await scrollForMoreProfileNotes(
            taskId,
            doc,
            win,
            scope,
            baseUrl,
            result.notes,
            maxItemsPerScope,
            maxScrollRounds,
            scrollWaitMs,
            maxStagnantScrollRounds,
          )
        : {
            notes: result.notes,
            scrollRounds: 0,
            stagnantRounds: result.stagnant_rounds,
            scrollMetrics: [],
          };
    result.notes = scrolled.notes;
    result.after_count = scrolled.notes.length || result.after_count;
    result.scroll_rounds = scrolled.scrollRounds;
    result.stagnant_rounds = scrolled.stagnantRounds;
    result.scroll_metrics = scrolled.scrollMetrics;
    const { notes, ...debugResult } = result;
    tabResults[scope] = debugResult;
    domNotes.push(...result.notes);
  }
  return { notes: domNotes, tabResults };
}

function buildTabCandidateDebug(doc: Document): Partial<Record<XhsBootstrapScope, boolean>> {
  return {
    saved: findProfileTab(doc, bootstrapProfileTabLabels("saved")) !== null,
    liked: findProfileTab(doc, bootstrapProfileTabLabels("liked")) !== null,
  };
}

async function executeTaskInPage(
  msg: TaskExecuteMessage,
  win: Window,
  doc: Document,
): Promise<TaskResultPayload> {
  try {
    if (detectXhsTaskLoginRequired(doc)) {
      return loginRequiredTaskResult(msg, win);
    }
    if (msg.type === "bootstrap_profile") {
      const immediateRisk = detectXhsTaskRiskControl(doc);
      if (immediateRisk) {
        return rateLimitedTaskResult(msg.task_id, immediateRisk);
      }
      const result = await executeBootstrapTaskInPage(msg, win, doc);
      if (result.status === "empty") {
        const emptyPageRisk = detectXhsTaskRiskControl(doc, {
          includePageText: true,
        });
        if (emptyPageRisk) {
          return rateLimitedTaskResult(msg.task_id, emptyPageRisk);
        }
      }
      return result;
    }

    const immediateRisk = detectXhsTaskRiskControl(doc);
    if (immediateRisk) {
      return rateLimitedTaskResult(msg.task_id, immediateRisk);
    }
    const source =
      msg.type === "search"
        ? await waitForSearchResponseOrCards(doc, win)
        : (await waitForCards(doc))
          ? "cards"
          : "empty";
    if (source === "empty") {
      if (detectXhsTaskLoginRequired(doc)) {
        return loginRequiredTaskResult(msg, win);
      }
      const emptyPageRisk = detectXhsTaskRiskControl(doc, {
        includePageText: true,
      });
      if (emptyPageRisk) {
        return rateLimitedTaskResult(msg.task_id, emptyPageRisk);
      }
      return emptySearchTaskResult(msg, win, doc, "no_note_anchor_after_wait");
    }

    const baseUrl = win.location.href;
    const responseNotes = source === "response" ? readXhsSearchResponseNotes() : [];
    const anchors = source === "cards" ? snapshotAllAnchors(doc) : [];
    const viewport = buildLargeViewport(win);
    const urls =
      source === "response"
        ? responseNotes.map((note) => note.url).slice(0, MAX_URLS)
        : collectInViewportNoteUrls(anchors, viewport, {
            baseUrl,
            toleranceBelowPx: 500,
            toleranceAbovePx: 500,
          });

    if (urls.length === 0) {
      const emptyPageRisk = detectXhsTaskRiskControl(doc, {
        includePageText: true,
      });
      if (emptyPageRisk) {
        return rateLimitedTaskResult(msg.task_id, emptyPageRisk);
      }
      return emptySearchTaskResult(msg, win, doc, "no_urls_in_viewport");
    }

    // Extract metadata from DOM for each discovered URL
    const urlSet = new Set(urls.slice(0, MAX_URLS));
    const notes: XhsNoteMetadata[] = responseNotes.map((note) => ({ ...note }));
    if (source === "cards") {
      const anchorEls = doc.querySelectorAll<HTMLAnchorElement>(NOTE_ANCHOR_SELECTOR);
      anchorEls.forEach((el) => {
        const meta = extractNoteMetadataFromAnchor(el, baseUrl);
        if (meta && urlSet.has(meta.url)) {
          notes.push(meta);
          urlSet.delete(meta.url);
        }
      });
    }

    // v0.3.10+: search / creator pages expose the same logged-in
    // user fingerprint via __INITIAL_STATE__. Capture + scrape-time
    // drop self-authored notes — XHS's search feed routinely returns
    // the user's own posts to the user themselves.
    const state = extractBootstrapStateFromDocument(doc);
    const selfInfo = state ? extractSelfInfoFromState(state) : null;
    const filteredNotes = filterSelfAuthoredNotes(notes, selfInfo);

    // Exact publish time: profile API payloads (`user_posted`) are buffered by
    // the MAIN-world sniffer; search / collect cards expose no time at all, so
    // a non-"all" date preference additionally fetches a bounded number of
    // note-detail HTML pages and reads noteDetailMap[noteId].note.time.
    const snifferMerged = mergePublishedTimes(filteredNotes, readXhsPublishedTimes());
    const publishedAtStats = { targets: 0, attempted: 0, enriched: 0 };
    let fetchedPublishedAt = 0;
    if (msg.need_published_at) {
      fetchedPublishedAt = await enrichNotesWithPublishedAt(filteredNotes, {
        maxNotes: PUBLISHED_AT_ENRICH_MAX_NOTES,
        timeoutMs: PUBLISHED_AT_ENRICH_TIMEOUT_MS,
        stats: publishedAtStats,
      });
    }

    // Background tabs never upgrade lazy-loaded card images past their
    // data: placeholder, so DOM extraction alone yields coverless notes on
    // exactly the search/creator paths this executor serves. Backfill real
    // CDN URLs from __INITIAL_STATE__, then harvest the bytes while the
    // page (and the URL token) is fresh. Both steps are best-effort.
    backfillCoverUrlsFromState(filteredNotes, state);
    await attachCoverData(filteredNotes);

    const result: TaskResultPayload = {
      task_id: msg.task_id,
      urls: urls.slice(0, MAX_URLS),
      notes: filteredNotes,
      status: "ok",
      debug: {
        xhs_discovery: {
          source: source === "response" ? "search_api" : "rendered_dom",
          task_type: msg.type,
          published_at_requested: Boolean(msg.need_published_at),
          published_at_targets: publishedAtStats.targets,
          published_at_attempted: publishedAtStats.attempted,
          published_at_sniffer_merged: snifferMerged,
          published_at_fetched: fetchedPublishedAt,
          published_at_total: filteredNotes.filter((note) => Boolean(note.published_at)).length,
        },
      },
    };
    if (selfInfo) {
      result.self_info = selfInfo;
    }
    return result;
  } catch (err) {
    return {
      task_id: msg.task_id,
      urls: [],
      notes: [],
      status: "error",
      error: String(err),
    };
  }
}

export async function executeBootstrapTaskInPage(
  msg: TaskExecuteMessage,
  win: Window,
  doc: Document,
): Promise<TaskResultPayload> {
  const scopes = normalizeBootstrapScopes(msg.scopes);
  const maxItemsPerScope = Math.max(1, msg.max_items_per_scope ?? 300);
  const maxScrollRounds = normalizeBootstrapScrollRounds(msg.max_scroll_rounds);
  const scrollWaitMs = normalizeBootstrapScrollWaitMs(msg.scroll_wait_ms);
  const maxStagnantScrollRounds = normalizeBootstrapStagnantScrollRounds(
    msg.max_stagnant_scroll_rounds,
  );
  const baseUrl = win.location.href || "https://www.xiaohongshu.com/explore";
  const is_profile_page = isProfilePage(baseUrl);
  const profileContentReady = is_profile_page
    ? await waitForBootstrapProfileContent(doc)
    : undefined;
  let state = extractBootstrapStateFromDocument(doc);
  const requested_scopes = [...scopes];
  const initialStateCounts = state
    ? countBootstrapStateNotesByScope(state, scopes, { baseUrl, maxItemsPerScope })
    : buildEmptyStateCounts(scopes);
  // v0.3.48+: capture self user_id + nickname so backend can filter
  // self-authored notes from XHS search / explore / saved-author paths.
  const selfInfo = state ? extractSelfInfoFromState(state) : null;

  if (!is_profile_page && (scopes.includes("saved") || scopes.includes("liked"))) {
    const profileUrlFromDocument = extractOwnProfileUrlFromDocument(doc, baseUrl);
    const profileUrlFromState = state ? extractOwnProfileUrlFromState(state, baseUrl) : "";
    const profileUrl = profileUrlFromDocument || profileUrlFromState;
    if (profileUrl) {
      const clickedProfileLink =
        maxScrollRounds > 0 && profileUrlFromDocument
          ? scheduleOwnProfileNavigationClick(doc, win, baseUrl)
          : false;
      return {
        task_id: msg.task_id,
        urls: [],
        notes: [],
        scope_counts: buildScopeCounts(scopes),
        status: "empty",
        next_url: profileUrl,
        debug: buildBootstrapDebugPayload({
          page_url: baseUrl,
          is_profile_page,
          has_initial_state: state !== null,
          requested_scopes,
          state_counts: initialStateCounts,
          profile_url_found: true,
          profile_url_source: profileUrlFromDocument ? "document" : "state",
          next_url_requested: true,
          next_url_clicked: clickedProfileLink,
          self_info: selfInfo ?? undefined,
        }) as unknown as Record<string, unknown>,
      };
    }
  }

  let domNotes: XhsBootstrapNote[] = [];
  let tabResults: Record<string, ProfileTabDebugResult> = {};
  if (is_profile_page) {
    const loaded = await loadProfileTabsForScopes(
      msg.task_id,
      scopes,
      doc,
      win,
      baseUrl,
      maxItemsPerScope,
      maxScrollRounds,
      scrollWaitMs,
      maxStagnantScrollRounds,
    );
    domNotes = loaded.notes;
    tabResults = loaded.tabResults;
    state = extractBootstrapStateFromDocument(doc);
  }

  const stateNotes = state
    ? extractBootstrapNotesFromState(state, scopes, { baseUrl, maxItemsPerScope })
    : [];

  // Do not treat the ordinary explore feed as browsing history. Only explicit
  // Xiaohongshu history/footprint state paths should become xhs_history.
  const notes = mergeBootstrapNotes([...stateNotes, ...domNotes], scopes, {
    maxItemsPerScope,
  });
  mergePublishedTimes(notes, readXhsPublishedTimes());
  const urls = [...new Set(notes.map((note) => note.url).filter(Boolean))];
  const scope_counts = buildScopeCounts(scopes, notes);
  const finalStateCounts = state
    ? countBootstrapStateNotesByScope(state, scopes, { baseUrl, maxItemsPerScope })
    : buildEmptyStateCounts(scopes);

  // Self info may be late-bound: on the initial /explore landing it
  // is not in state, but after navigating to the user's profile we
  // re-read state above and can capture it now.
  const finalSelfInfo = selfInfo ?? (state ? extractSelfInfoFromState(state) : null);

  return {
    task_id: msg.task_id,
    urls,
    notes,
    scope_counts,
    status: notes.length > 0 ? "ok" : "empty",
      debug: buildBootstrapDebugPayload({
        page_url: baseUrl,
        is_profile_page,
        has_initial_state: state !== null,
        profile_content_ready: profileContentReady,
        requested_scopes,
        state_counts: finalStateCounts,
      dom_counts: is_profile_page ? buildScopeCounts(scopes, domNotes) : undefined,
      tab_candidate_texts: is_profile_page ? collectProfileTabCandidateTexts(doc) : undefined,
      scroll_candidates: is_profile_page ? collectBootstrapScrollCandidates(doc, 12) : undefined,
      tab_load_results: is_profile_page ? tabResults : undefined,
      profile_url_found: is_profile_page ? undefined : false,
      profile_url_source: is_profile_page ? undefined : "",
      next_url_requested: false,
      tab_candidates: is_profile_page ? buildTabCandidateDebug(doc) : undefined,
      self_info: finalSelfInfo ?? undefined,
    }) as unknown as Record<string, unknown>,
  };
}

/**
 * Register the message listener that the background dispatcher uses to
 * trigger task execution. Call once from the xhs content-script entry.
 */
export function registerTaskExecutor(): void {
  chrome.runtime.onMessage.addListener(
    (message: Record<string, unknown>, _sender, sendResponse) => {
      if (message.action !== "XHS_TASK_EXECUTE") return false;

      const data = message.data as TaskExecuteMessage | undefined;
      if (!data?.task_id) return false;

      // Run async, then post the result through the same acked path as partial
      // batches so MV3 does not drop the background POST before it settles.
      void executeTaskInPage(data, window, document).then((result) => {
        void sendTaskResult(result);
      });

      // Return false — we don't use sendResponse.
      return false;
    },
  );
}
