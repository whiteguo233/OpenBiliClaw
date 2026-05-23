/**
 * xhs task executor — content-script side.
 *
 * When the background dispatcher opens a tab with a search or creator
 * page, this module waits for note cards to render (MutationObserver,
 * 5 s hard cap), extracts up to 20 note URLs from the initial viewport
 * plus immediately adjacent DOM, and emits `XHS_TASK_RESULT` back to
 * the service worker.
 *
 * Bootstrap profile imports can optionally scroll when the backend requests
 * it, but passive search/creator collection still only reads rendered cards.
 */

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

const MAX_URLS = 20;
const RENDER_WAIT_MS = 5_000;
const CHECK_INTERVAL_MS = 300;
const PROFILE_CLICK_DELAY_MS = 150;
const PROFILE_CONTENT_WAIT_MS = 8_000;
const ANCHOR_SELECTOR = 'a[href*="/explore/"], a[href*="/discovery/item/"]';

export interface TaskExecuteMessage {
  task_id: string;
  type: "search" | "creator" | "bootstrap_profile";
  scopes?: XhsBootstrapScope[];
  max_items_per_scope?: number;
  max_scroll_rounds?: number;
  scroll_wait_ms?: number;
  max_stagnant_scroll_rounds?: number;
}

export interface TaskResultPayload {
  task_id: string;
  urls: string[];
  notes: Array<XhsNoteMetadata | XhsBootstrapNote>;
  scope_counts?: Record<string, number>;
  status: "ok" | "empty" | "partial" | "error";
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
  const nodes = root.querySelectorAll<HTMLAnchorElement>(ANCHOR_SELECTOR);
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

// ---------------------------------------------------------------------------
// Chrome integration
// ---------------------------------------------------------------------------

function waitForCards(doc: Document): Promise<boolean> {
  return new Promise((resolve) => {
    // Quick check — cards may already be present.
    if (doc.querySelectorAll(ANCHOR_SELECTOR).length > 0) {
      resolve(true);
      return;
    }

    let settled = false;
    const observer = new MutationObserver(() => {
      if (doc.querySelectorAll(ANCHOR_SELECTOR).length > 0) {
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
      if (doc.querySelectorAll(ANCHOR_SELECTOR).length > 0) {
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
        resolve(doc.querySelectorAll(ANCHOR_SELECTOR).length > 0);
      }
    }, RENDER_WAIT_MS);
  });
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
    if (!labels) continue;
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
    if (msg.type === "bootstrap_profile") {
      return executeBootstrapTaskInPage(msg, win, doc);
    }

    const found = await waitForCards(doc);
    if (!found) {
      return { task_id: msg.task_id, urls: [], notes: [], status: "empty" };
    }

    const anchors = snapshotAllAnchors(doc);
    const viewport = buildLargeViewport(win);
    const baseUrl = win.location.href;
    const urls = collectInViewportNoteUrls(anchors, viewport, {
      baseUrl,
      toleranceBelowPx: 500,
      toleranceAbovePx: 500,
    });

    if (urls.length === 0) {
      return { task_id: msg.task_id, urls: [], notes: [], status: "empty" };
    }

    // Extract metadata from DOM for each discovered URL
    const urlSet = new Set(urls.slice(0, MAX_URLS));
    const notes: XhsNoteMetadata[] = [];
    const anchorEls = doc.querySelectorAll<HTMLAnchorElement>(ANCHOR_SELECTOR);
    anchorEls.forEach((el) => {
      const meta = extractNoteMetadataFromAnchor(el, baseUrl);
      if (meta && urlSet.has(meta.url)) {
        notes.push(meta);
        urlSet.delete(meta.url);
      }
    });

    // v0.3.10+: search / creator pages expose the same logged-in
    // user fingerprint via __INITIAL_STATE__. Capture + scrape-time
    // drop self-authored notes — XHS's search feed routinely returns
    // the user's own posts to the user themselves.
    const state = extractBootstrapStateFromDocument(doc);
    const selfInfo = state ? extractSelfInfoFromState(state) : null;
    const filteredNotes = filterSelfAuthoredNotes(notes, selfInfo);

    const result: TaskResultPayload = {
      task_id: msg.task_id,
      urls: urls.slice(0, MAX_URLS),
      notes: filteredNotes,
      status: "ok",
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
