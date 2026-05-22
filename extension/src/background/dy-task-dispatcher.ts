/**
 * Douyin task dispatcher — background polling for bootstrap_profile tasks.
 *
 * Task 5 of the Douyin bootstrap import plan
 * (docs/plans/2026-05-06-douyin-bootstrap-import.md). Module isolation:
 * zero imports from xhs-task-dispatcher; the dy/ tree owns its own
 * lifecycle so divergence is allowed.
 *
 * Polls `GET /api/sources/dy/next-task` at intervals. When the backend
 * hands out a task, the dispatcher:
 *   1. Opens a Douyin tab. Discovery tasks stay in the background;
 *      bootstrap_profile remains foreground because it imports the
 *      user's own account signals.
 *   2. Listens for `DY_TASK_RESULT` messages from the content script
 *      (partial + final).
 *   3. POSTs each result back to `/api/sources/dy/task-result`.
 *   4. Closes the tab on the final (status=ok / failed / empty) result
 *      or on timeout.
 *   5. Waits ``DEFAULT_POLL_INTERVAL_MS`` before asking for the next.
 *
 * Only one task is in flight at a time (mutex). Bootstrap tasks get a
 * generous timeout because each scope can scroll up to 15 rounds and
 * we navigate through 4 scopes serially.
 */

import type {
  DouyinBootstrapItem,
  DouyinScope,
  DouyinSearchItem,
} from "../main/dy-fetch-tap.js";
import { apiUrl } from "../shared/backend-endpoint.ts";
// Cross-source mutex via globalThis. Mirror of the helper inlined
// in xhs-task-dispatcher; both dispatchers coordinate by writing to
// the same field on globalThis. See dispatcher-mutex.ts for the
// canonical reference (kept as documentation, not an import target).
const _MUTEX_STALE_MS = 6 * 60 * 1000;
function tryAcquireDispatcherMutex(label: string): boolean {
  const g = globalThis as unknown as {
    __OBC_DISPATCHER_MUTEX_HOLDER__?: string;
    __OBC_DISPATCHER_MUTEX_HELD_SINCE__?: number;
  };
  if (g.__OBC_DISPATCHER_MUTEX_HOLDER__) {
    if (Date.now() - (g.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ ?? 0) > _MUTEX_STALE_MS) {
      g.__OBC_DISPATCHER_MUTEX_HOLDER__ = undefined;
    } else {
      return false;
    }
  }
  g.__OBC_DISPATCHER_MUTEX_HOLDER__ = label;
  g.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = Date.now();
  return true;
}
function releaseDispatcherMutex(label: string): void {
  const g = globalThis as unknown as {
    __OBC_DISPATCHER_MUTEX_HOLDER__?: string;
    __OBC_DISPATCHER_MUTEX_HELD_SINCE__?: number;
  };
  if (g.__OBC_DISPATCHER_MUTEX_HOLDER__ === label) {
    g.__OBC_DISPATCHER_MUTEX_HOLDER__ = undefined;
    g.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = undefined;
  }
}

// TEMP DEBUG: extension-side log relay → daemon (see debug-log.ts).
function debugLog(event: string, data?: unknown): void {
  void (async () => {
    try {
      await fetch(await apiUrl("/sources/_debug/log"), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ source: "dy", event, data: data ?? null }),
      });
    } catch {
      // ignore
    }
  })();
}

// buildScopeUrl is loaded lazily via dynamic import inside the
// chrome-lifecycle code path (executeTask / navigateToCurrentScope).
// Reason: node:test's --experimental-strip-types resolver can't follow
// `.js` paths into `.ts` source files when the importer is the
// dispatcher's own .ts source. Pure-helper unit tests (buildDyTaskUrl
// / isValidDyTask / computeDyTaskTimeoutMs / buildDyExecuteMessageData)
// don't touch the chrome path so they stay testable. The bundled
// extension (esbuild) inlines the dynamic import at build time, so
// production runtime is unaffected.
async function loadBuildScopeUrl(): Promise<
  (scope: DouyinScope, secUid: string) => string
> {
  const mod = await import("../content/dy/task-executor.js");
  return mod.buildScopeUrl;
}

const DEFAULT_POLL_INTERVAL_MS = 60_000;
const TASK_TIMEOUT_MS = 30_000;
const SEARCH_TASK_TIMEOUT_MS = 180_000;
const BOOTSTRAP_PER_ROUND_TIMEOUT_MS = 3_000;
const BOOTSTRAP_MAX_TASK_TIMEOUT_MS = 360_000;
const POLL_ALARM_NAME = "openbiliclaw-dy-task-poll";

/**
 * Detect Safari by checking for the sidePanel API, which Safari does not
 * support. Without this check, every task dispatches a new foreground tab
 * on Safari (no sidePanel to hide in), flooding the user's browser with
 * tabs that never close cleanly.
 */
function _isSafari(): boolean {
  return (
    typeof chrome.sidePanel === "undefined" ||
    typeof (chrome.sidePanel as { open?: unknown }).open !== "function"
  );
}

const KNOWN_SCOPES: readonly DouyinScope[] = [
  "dy_post",
  "dy_collect",
  "dy_like",
  "dy_follow",
] as const;

export interface DyTask {
  id: string;
  type: "bootstrap_profile" | "search" | "hot" | "feed";
  scopes?: DouyinScope[];
  max_items_per_scope?: number;
  max_scroll_rounds?: number;
  max_stagnant_scroll_rounds?: number;
  keywords?: string[];
  max_items_per_keyword?: number;
  hot_items?: DyHotTaskItem[];
  max_items_per_hot?: number;
  max_items?: number;
}

export interface DyHotTaskItem {
  word?: string;
  sentence_id: string;
  hot_value?: number;
}

export interface DyTaskResult {
  task_id: string;
  status: "ok" | "empty" | "partial" | "failed";
  videos?: unknown[];
  scope_counts?: Record<string, number>;
  error?: string;
  debug?: Record<string, unknown>;
}

let taskInFlight = false;
let taskTabId: number | null = null;
let ownsTaskTab = false;
let taskTimeoutId: ReturnType<typeof setTimeout> | null = null;
let currentTask: DyTask | null = null;

// Per-scope state machine. Bootstrap visits 4 profile sub-tabs
// serially (post → collect → like → follow); each sub-tab is its
// own URL load + DY_SCOPE_EXECUTE round-trip with the content
// script. Scope counts accumulate across sub-tabs so the final
// status=ok payload carries the full picture.
interface TaskProgress {
  task_id: string;
  scopes: DouyinScope[];
  current_scope_idx: number;
  accumulated_counts: Record<DouyinScope, number>;
  max_items_per_scope: number;
  max_scroll_rounds: number;
  max_stagnant_scroll_rounds: number;
}

let progress: TaskProgress | null = null;

interface SearchProgress {
  task_id: string;
  keywords: string[];
  current_keyword_idx: number;
  accumulated_count: number;
  max_items_per_keyword: number;
}

let searchProgress: SearchProgress | null = null;

interface HotProgress {
  task_id: string;
  hot_items: DyHotTaskItem[];
  current_hot_idx: number;
  accumulated_count: number;
  max_items_per_hot: number;
  max_items_total: number;
}

let hotProgress: HotProgress | null = null;

interface FeedProgress {
  task_id: string;
  accumulated_count: number;
  max_items: number;
}

let feedProgress: FeedProgress | null = null;

// ---------------------------------------------------------------------------
// Pure helpers (testable without chrome)
// ---------------------------------------------------------------------------

export function buildDyTaskUrl(task: DyTask): string | null {
  if (task.type === "bootstrap_profile") {
    return "https://www.douyin.com/";
  }
  if (task.type === "search") {
    return "https://www.douyin.com/";
  }
  if (task.type === "hot") {
    return "https://www.douyin.com/";
  }
  if (task.type === "feed") {
    return "https://www.douyin.com/";
  }
  return null;
}

export function isValidDyTask(task: unknown): task is DyTask {
  if (typeof task !== "object" || task === null) return false;
  const t = task as Record<string, unknown>;
  if (typeof t.id !== "string" || !t.id) return false;
  if (t.type === "search") {
    if (!Array.isArray(t.keywords)) return false;
    return t.keywords.some((keyword) => typeof keyword === "string" && keyword.trim());
  }
  if (t.type === "hot") {
    if (!Array.isArray(t.hot_items)) return false;
    return t.hot_items.some((item) => {
      if (!item || typeof item !== "object") return false;
      const row = item as Record<string, unknown>;
      return typeof row.sentence_id === "string" && Boolean(row.sentence_id.trim());
    });
  }
  if (t.type === "feed") {
    if (t.max_items === undefined) return true;
    return typeof t.max_items === "number" && Number.isFinite(t.max_items) && t.max_items > 0;
  }
  if (t.type !== "bootstrap_profile") return false;
  if (t.scopes !== undefined) {
    if (!Array.isArray(t.scopes)) return false;
    for (const s of t.scopes) {
      if (!KNOWN_SCOPES.includes(s as DouyinScope)) return false;
    }
  }
  return true;
}

export function computeDyTaskTimeoutMs(task: DyTask): number {
  if (task.type === "search") {
    const keywordCount =
      Array.isArray(task.keywords) && task.keywords.length > 0 ? task.keywords.length : 1;
    return Math.min(
      Math.max(SEARCH_TASK_TIMEOUT_MS, keywordCount * SEARCH_TASK_TIMEOUT_MS),
      BOOTSTRAP_MAX_TASK_TIMEOUT_MS,
    );
  }
  if (task.type === "hot") {
    const hotCount =
      Array.isArray(task.hot_items) && task.hot_items.length > 0 ? task.hot_items.length : 1;
    return Math.min(
      Math.max(TASK_TIMEOUT_MS, TASK_TIMEOUT_MS + hotCount * 70_000),
      BOOTSTRAP_MAX_TASK_TIMEOUT_MS,
    );
  }
  if (task.type === "feed") {
    return Math.min(Math.max(TASK_TIMEOUT_MS, 60_000), BOOTSTRAP_MAX_TASK_TIMEOUT_MS);
  }
  // Default per-task timeout has to account for the executor visiting
  // up to 4 scope tabs in series, each scrolling up to N rounds. We
  // assume 4 scopes if the task didn't enumerate them — the CLI's
  // default invocation does NOT pass scopes explicitly today, so
  // dropping below 4 here would silently squeeze the budget.
  const scopeCount = Array.isArray(task.scopes) && task.scopes.length > 0
    ? task.scopes.length
    : 4;
  const rounds =
    typeof task.max_scroll_rounds === "number" && Number.isFinite(task.max_scroll_rounds)
      ? Math.max(0, Math.floor(task.max_scroll_rounds))
      : 0;
  const scrollBudget = scopeCount * rounds * BOOTSTRAP_PER_ROUND_TIMEOUT_MS;
  return Math.min(
    Math.max(TASK_TIMEOUT_MS, TASK_TIMEOUT_MS + scrollBudget),
    BOOTSTRAP_MAX_TASK_TIMEOUT_MS,
  );
}

export function buildDyExecuteMessageData(task: DyTask): Record<string, unknown> {
  const data: Record<string, unknown> = { task_id: task.id, type: task.type };
  if (task.scopes !== undefined) data.scopes = task.scopes;
  if (task.max_items_per_scope !== undefined) {
    data.max_items_per_scope = task.max_items_per_scope;
  }
  if (task.max_scroll_rounds !== undefined) data.max_scroll_rounds = task.max_scroll_rounds;
  if (task.max_stagnant_scroll_rounds !== undefined) {
    data.max_stagnant_scroll_rounds = task.max_stagnant_scroll_rounds;
  }
  if (task.keywords !== undefined) data.keywords = task.keywords;
  if (task.max_items_per_keyword !== undefined) {
    data.max_items_per_keyword = task.max_items_per_keyword;
  }
  if (task.hot_items !== undefined) data.hot_items = task.hot_items;
  if (task.max_items_per_hot !== undefined) {
    data.max_items_per_hot = task.max_items_per_hot;
  }
  if (task.max_items !== undefined) data.max_items = task.max_items;
  return data;
}

export function shouldFinalizeHotTask({
  accumulatedCount,
  maxItemsTotal,
  currentHotIndex,
  hotItemCount,
}: {
  accumulatedCount: number;
  maxItemsTotal: number;
  currentHotIndex: number;
  hotItemCount: number;
}): boolean {
  return accumulatedCount >= maxItemsTotal || currentHotIndex + 1 >= hotItemCount;
}

export function shouldOpenDyTaskActive(task: DyTask): boolean {
  return task.type === "bootstrap_profile";
}

// ---------------------------------------------------------------------------
// Chrome lifecycle (not unit-tested — Task 4's chrome-devtools MCP probe
// already exercised the highest-risk seam against real douyin.com).
// ---------------------------------------------------------------------------

async function fetchNextTask(): Promise<DyTask | null> {
  try {
    const resp = await fetch(await apiUrl("/sources/dy/next-task"));
    if (resp.status === 204) return null; // no pending task
    if (!resp.ok) return null;
    const payload: unknown = await resp.json();
    return isValidDyTask(payload) ? payload : null;
  } catch {
    return null;
  }
}

async function postTaskResult(result: DyTaskResult): Promise<void> {
  try {
    await fetch(await apiUrl("/sources/dy/task-result"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch {
    // Backend transient unavailability — drop the result rather than
    // crashing the dispatcher. The next task poll will keep things moving.
  }
}

function cleanupTask(): void {
  if (taskTimeoutId !== null) {
    clearTimeout(taskTimeoutId);
    taskTimeoutId = null;
  }
  if (ownsTaskTab && taskTabId !== null) {
    try {
      chrome.tabs.remove(taskTabId);
    } catch {
      // Tab may already be closed; ignore.
    }
  }
  taskTabId = null;
  ownsTaskTab = false;
  currentTask = null;
  progress = null;
  searchProgress = null;
  hotProgress = null;
  feedProgress = null;
  taskInFlight = false;
  releaseDispatcherMutex("dy");
}

function emptyScopeCounts(): Record<DouyinScope, number> {
  return { dy_post: 0, dy_collect: 0, dy_like: 0, dy_follow: 0 };
}

function armTaskTimeout(task: DyTask): void {
  const timeoutMs = computeDyTaskTimeoutMs(task);
  taskTimeoutId = setTimeout(async () => {
    await postTaskResult({
      task_id: task.id,
      status: "failed",
      error: "task_timeout",
    });
    cleanupTask();
  }, timeoutMs);
}

// ---------------------------------------------------------------------------
// Per-scope state-machine driver
// ---------------------------------------------------------------------------

/**
 * Wait for tab.status === "complete" then run the callback. Cleans
 * itself up on first complete signal so intra-page navigations don't
 * re-trigger the handshake.
 */
export function onTabReady(
  tabId: number,
  callback: () => void,
  options: { fallbackMs?: number } = {},
): void {
  let completed = false;
  let fallbackTimer: ReturnType<typeof setTimeout> | null = null;
  const listener = (updatedId: number, info: { status?: string }): void => {
    if (updatedId !== tabId) return;
    if (info.status !== "complete") return;
    runOnce();
  };
  const runOnce = (): void => {
    if (completed) return;
    completed = true;
    if (fallbackTimer !== null) {
      clearTimeout(fallbackTimer);
      fallbackTimer = null;
    }
    chrome.tabs.onUpdated.removeListener(listener);
    callback();
  };
  chrome.tabs.onUpdated.addListener(listener);
  if (
    typeof options.fallbackMs === "number" &&
    Number.isFinite(options.fallbackMs) &&
    options.fallbackMs >= 0
  ) {
    fallbackTimer = setTimeout(runOnce, options.fallbackMs);
  }
  void chrome.tabs.get(tabId).then((tab) => {
    if (tab.status === "complete") runOnce();
  }).catch(() => {
    // Tab may close between create/update and readiness probing. The timeout
    // path will report the task failure if no completion event arrives.
  });
}

/**
 * Send DY_SCOPE_EXECUTE to the content script for the current scope.
 * Failure to deliver (no listener / wrong URL / CSP) is converted into
 * an empty DY_SCOPE_RESULT so the state machine still advances and
 * the task eventually finalises rather than hanging until timeout.
 */
// TEMP DEBUG: track the most recent injectFetchTapInto outcome so
// it can be passed through DY_SCOPE_EXECUTE → content script →
// DY_SCOPE_RESULT → backend logs. Lets us diagnose
// install_messages_received=0 without needing the user's browser
// console. Will be reverted before release.
let _lastInjectStatus: string = "not_attempted";

function sendScopeExecuteMessage(): void {
  if (!progress || !taskTabId) {
    debugLog("sendScopeExecute:no_progress_or_tab", {
      hasProgress: !!progress,
      taskTabId,
    });
    return;
  }
  const scope = progress.scopes[progress.current_scope_idx];
  if (!scope) {
    debugLog("sendScopeExecute:no_scope_at_idx", {
      idx: progress.current_scope_idx,
    });
    return;
  }
  debugLog("sendScopeExecute:start", { scope, idx: progress.current_scope_idx });
  void chrome.tabs
    .sendMessage(taskTabId, {
      action: "DY_SCOPE_EXECUTE",
      data: {
        task_id: progress.task_id,
        scope,
        max_items_per_scope: progress.max_items_per_scope,
        max_scroll_rounds: progress.max_scroll_rounds,
        max_stagnant_scroll_rounds: progress.max_stagnant_scroll_rounds,
        debug_inject_status: _lastInjectStatus,
      },
    })
    .catch((err) => {
      debugLog("sendScopeExecute:sendMessage_failed", { error: String(err) });
      // Synthesise an empty per-scope result so the state machine
      // still advances; this is what we'd see if the user landed
      // on a Douyin login wall or risk-control page where our
      // content script isn't allowed to register.
      void handleDyScopeResult({
        task_id: progress!.task_id,
        scope,
        items: [],
        scope_count: 0,
        status: "failed",
        error: "sendMessage_failed",
      });
    });
}

function buildSearchPageUrl(keyword: string): string {
  return `https://www.douyin.com/search/${encodeURIComponent(keyword)}?type=video`;
}

function buildHotPageUrl(sentenceId: string): string {
  return `https://www.douyin.com/hot/${encodeURIComponent(sentenceId)}`;
}

function sendSearchExecuteMessage(): void {
  if (!searchProgress || !taskTabId) return;
  const keyword = searchProgress.keywords[searchProgress.current_keyword_idx];
  if (!keyword) return;
  void chrome.tabs
    .sendMessage(taskTabId, {
      action: "DY_SEARCH_EXECUTE",
      data: {
        task_id: searchProgress.task_id,
        keyword,
        max_items: searchProgress.max_items_per_keyword,
        debug_inject_status: _lastInjectStatus,
      },
    })
    .catch((err) => {
      void handleDySearchResult({
        task_id: searchProgress!.task_id,
        keyword,
        items: [],
        scope_count: searchProgress!.accumulated_count,
        status: "failed",
        error: `sendMessage_failed: ${String(err)}`,
      });
    });
}

function sendHotExecuteMessage(): void {
  if (!hotProgress || !taskTabId) return;
  const hotItem = hotProgress.hot_items[hotProgress.current_hot_idx];
  if (!hotItem) return;
  void chrome.tabs
    .sendMessage(taskTabId, {
      action: "DY_HOT_EXECUTE",
      data: {
        task_id: hotProgress.task_id,
        sentence_id: hotItem.sentence_id,
        word: hotItem.word ?? "",
        max_items: hotProgress.max_items_per_hot,
        debug_inject_status: _lastInjectStatus,
      },
    })
    .catch((err) => {
      void handleDyHotResult({
        task_id: hotProgress!.task_id,
        sentence_id: hotItem.sentence_id,
        word: hotItem.word ?? "",
        items: [],
        scope_count: hotProgress!.accumulated_count,
        status: "failed",
        error: `sendMessage_failed: ${String(err)}`,
      });
    });
}

function sendFeedExecuteMessage(): void {
  if (!feedProgress || !taskTabId) return;
  void chrome.tabs
    .sendMessage(taskTabId, {
      action: "DY_FEED_EXECUTE",
      data: {
        task_id: feedProgress.task_id,
        max_items: feedProgress.max_items,
        debug_inject_status: _lastInjectStatus,
      },
    })
    .catch((err) => {
      void handleDyFeedResult({
        task_id: feedProgress!.task_id,
        items: [],
        scope_count: feedProgress!.accumulated_count,
        status: "failed",
        error: `sendMessage_failed: ${String(err)}`,
      });
    });
}

function navigateToCurrentSearch(): void {
  if (!searchProgress || taskTabId === null) return;
  const keyword = searchProgress.keywords[searchProgress.current_keyword_idx];
  if (!keyword) return;
  chrome.tabs.update(taskTabId, { url: buildSearchPageUrl(keyword) }, () => {
    onTabReady(taskTabId!, () => {
      void injectFetchTapInto(taskTabId!).then(() => {
        debugLog("executeSearchTask:inject_done", { inject_status: _lastInjectStatus });
        sendSearchExecuteMessage();
      });
    }, { fallbackMs: 8_000 });
  });
}

function navigateToCurrentHot(): void {
  if (!hotProgress || taskTabId === null) return;
  const hotItem = hotProgress.hot_items[hotProgress.current_hot_idx];
  if (!hotItem) return;
  chrome.tabs.update(taskTabId, { url: buildHotPageUrl(hotItem.sentence_id) }, () => {
    onTabReady(taskTabId!, () => {
      void injectFetchTapInto(taskTabId!).then(() => {
        debugLog("executeHotTask:inject_done", { inject_status: _lastInjectStatus });
        sendHotExecuteMessage();
      });
    }, { fallbackMs: 10_000 });
  });
}

function navigateToFeed(): void {
  if (!feedProgress || taskTabId === null) return;
  chrome.tabs.update(taskTabId, { url: "https://www.douyin.com/" }, () => {
    onTabReady(taskTabId!, () => {
      void injectFetchTapInto(taskTabId!).then(() => {
        debugLog("executeFeedTask:inject_done", { inject_status: _lastInjectStatus });
        sendFeedExecuteMessage();
      });
    }, { fallbackMs: 8_000 });
  });
}

function navigateToCurrentScope(): void {
  // Click-driven navigation lives entirely in the content script
  // (douyin.ts: clickToScope). Dispatcher just hands it off — no
  // chrome.tabs.update, no fresh document commit, no need to re-
  // inject fetch-tap (it's still installed from the homepage stage,
  // and SPA route within Douyin's React app doesn't unload it).
  // Risk control is happier because every nav is a real-looking
  // user click instead of a URL jump.
  if (!progress || taskTabId === null) return;
  sendScopeExecuteMessage();
}

async function injectFetchTapInto(tabId: number): Promise<void> {
  // Inject dy-fetch-tap.js into the MAIN world of the current tab.
  // This bypasses the manifest content_scripts injection logic so
  // SPA-route navs and any other Chrome-version-specific edge cases
  // don't matter — every scope gets a guaranteed fresh hook.
  if (typeof chrome === "undefined" || !chrome.scripting) {
    _lastInjectStatus = "scripting_api_missing";
    return;
  }
  try {
    const result = await chrome.scripting.executeScript({
      target: { tabId, allFrames: false },
      files: ["dist/main/dy-fetch-tap.js"],
      world: "MAIN",
    });
    _lastInjectStatus = `ok_results=${Array.isArray(result) ? result.length : "n/a"}`;
  } catch (err) {
    // Inject failed — could be scripting permission missing, file
    // not in web_accessible_resources, captcha intermediate page,
    // or chrome:// blocked. Capture the error so the content script
    // can ship it back through scope debug.
    _lastInjectStatus = `error: ${String(err).slice(0, 120)}`;
  }
}

function normalizeHotTaskItems(items: DyHotTaskItem[] | undefined): DyHotTaskItem[] {
  const seen = new Set<string>();
  const result: DyHotTaskItem[] = [];
  for (const item of items ?? []) {
    const sentenceId = String(item?.sentence_id ?? "").trim();
    if (!sentenceId || seen.has(sentenceId)) continue;
    seen.add(sentenceId);
    result.push({
      sentence_id: sentenceId,
      word: String(item.word ?? "").trim(),
      hot_value: item.hot_value,
    });
  }
  return result;
}

export async function executeTask(task: DyTask): Promise<void> {
  debugLog("executeTask:start", { task_id: task.id, taskInFlight });
  if (taskInFlight) {
    debugLog("executeTask:already_in_flight");
    return;
  }
  // Cross-source mutex — bail if another dispatcher is currently
  // holding the task slot. The next alarm fires in 60s and we'll
  // retry then. Without this guard, daemon producers can race with
  // a user's manual fetch and both dispatchers may fight over tabs.
  const mutexAcquired = tryAcquireDispatcherMutex("dy");
  debugLog("executeTask:mutex", { acquired: mutexAcquired });
  if (!mutexAcquired) return;
  taskInFlight = true;
  currentTask = task;

  if (task.type === "search") {
    const keywords = (task.keywords ?? [])
      .map((keyword) => String(keyword).trim())
      .filter((keyword, index, all) => keyword && all.indexOf(keyword) === index);
    searchProgress = {
      task_id: task.id,
      keywords,
      current_keyword_idx: 0,
      accumulated_count: 0,
      max_items_per_keyword: Math.max(1, Math.floor(task.max_items_per_keyword ?? 20)),
    };

    let tab: chrome.tabs.Tab;
    try {
      tab = await chrome.tabs.create({
        url: "https://www.douyin.com/",
        active: _isSafari() ? false : shouldOpenDyTaskActive(task),
      });
      debugLog("executeSearchTask:tab_created", { tabId: tab.id, keywords: keywords.length });
    } catch (err) {
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: "tab_create_failed",
      });
      cleanupTask();
      return;
    }
    taskTabId = tab.id ?? null;
    ownsTaskTab = true;
    armTaskTimeout(task);
    if (taskTabId === null || keywords.length === 0) {
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: taskTabId === null ? "tab_id_unknown" : "missing_keywords",
      });
      cleanupTask();
      return;
    }
    onTabReady(taskTabId, () => {
      navigateToCurrentSearch();
    }, { fallbackMs: 5_000 });
    return;
  }

  if (task.type === "hot") {
    const hotItems = normalizeHotTaskItems(task.hot_items);
    const maxItemsTotal = Math.max(1, Math.floor(task.max_items ?? task.max_items_per_hot ?? 20));
    hotProgress = {
      task_id: task.id,
      hot_items: hotItems,
      current_hot_idx: 0,
      accumulated_count: 0,
      max_items_per_hot: Math.max(
        1,
        Math.min(maxItemsTotal, Math.floor(task.max_items_per_hot ?? maxItemsTotal)),
      ),
      max_items_total: maxItemsTotal,
    };

    let tab: chrome.tabs.Tab;
    try {
      tab = await chrome.tabs.create({
        url: "https://www.douyin.com/",
        active: _isSafari() ? false : shouldOpenDyTaskActive(task),
      });
      debugLog("executeHotTask:tab_created", { tabId: tab.id, hot_count: hotItems.length });
    } catch (err) {
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: "tab_create_failed",
      });
      cleanupTask();
      return;
    }
    taskTabId = tab.id ?? null;
    ownsTaskTab = true;
    armTaskTimeout(task);
    if (taskTabId === null || hotItems.length === 0) {
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: taskTabId === null ? "tab_id_unknown" : "missing_hot_items",
      });
      cleanupTask();
      return;
    }
    onTabReady(taskTabId, () => {
      navigateToCurrentHot();
    }, { fallbackMs: 5_000 });
    return;
  }

  if (task.type === "feed") {
    feedProgress = {
      task_id: task.id,
      accumulated_count: 0,
      max_items: Math.max(1, Math.floor(task.max_items ?? 20)),
    };

    let tab: chrome.tabs.Tab;
    try {
      tab = await chrome.tabs.create({
        url: "https://www.douyin.com/",
        active: _isSafari() ? false : shouldOpenDyTaskActive(task),
      });
      debugLog("executeFeedTask:tab_created", { tabId: tab.id });
    } catch (err) {
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: "tab_create_failed",
      });
      cleanupTask();
      return;
    }
    taskTabId = tab.id ?? null;
    ownsTaskTab = true;
    armTaskTimeout(task);
    if (taskTabId === null) {
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: "tab_id_unknown",
      });
      cleanupTask();
      return;
    }
    onTabReady(taskTabId, () => {
      navigateToFeed();
    }, { fallbackMs: 5_000 });
    return;
  }

  const scopes: DouyinScope[] =
    task.scopes && task.scopes.length > 0
      ? task.scopes
      : ["dy_post", "dy_collect", "dy_like", "dy_follow"];
  progress = {
    task_id: task.id,
    scopes,
    current_scope_idx: 0,
    accumulated_counts: emptyScopeCounts(),
    max_items_per_scope: task.max_items_per_scope ?? 300,
    max_scroll_rounds: task.max_scroll_rounds ?? 15,
    max_stagnant_scroll_rounds: task.max_stagnant_scroll_rounds ?? 5,
  };

  // Open the Douyin homepage first instead of jumping straight to
  // /user/self. Direct profile-URL nav from a fresh tab tripped
  // Douyin's risk control on real-browser e2e (2026-05-08): user
  // saw the captcha intermediate page even when logged in. Routing
  // through the homepage lets page bundle / cookies / risk-score
  // settle naturally before we route to the profile, exactly the
  // way a user would land on their own profile (douyin.com → click
  // profile, not empty tab → /user/self).
  let tab: chrome.tabs.Tab;
  try {
    tab = await chrome.tabs.create({
      url: "https://www.douyin.com/",
      active: _isSafari() ? false : shouldOpenDyTaskActive(task),
    });
    debugLog("executeTask:tab_created", { tabId: tab.id });
  } catch (err) {
    debugLog("executeTask:tab_create_failed", { error: String(err) });
    await postTaskResult({
      task_id: task.id,
      status: "failed",
      error: "tab_create_failed",
    });
    cleanupTask();
    return;
  }
  taskTabId = tab.id ?? null;
  ownsTaskTab = true;
  armTaskTimeout(task);

  if (taskTabId === null) {
    await postTaskResult({
      task_id: task.id,
      status: "failed",
      error: "tab_id_unknown",
    });
    cleanupTask();
    return;
  }

  // Single-stage entry now — we land on douyin.com home, inject
  // fetch-tap once into MAIN world, then hand control to the
  // content-script's runScope. runScope clicks "我" then the
  // requested sub-tab (clickToScope), staying inside Douyin's SPA
  // session the whole time. No more chrome.tabs.update between
  // scopes; fetch-tap stays installed across SPA routes.
  onTabReady(taskTabId, () => {
    debugLog("executeTask:tab_ready", { tabId: taskTabId });
    void injectFetchTapInto(taskTabId!).then(() => {
      debugLog("executeTask:inject_done", { inject_status: _lastInjectStatus });
      sendScopeExecuteMessage();
    });
  });
}

/**
 * Per-scope result from the content script. Accumulates into the
 * task-level progress, posts a partial to the backend so memory
 * propagation happens incrementally, then either advances to the
 * next scope or finalises the task with status=ok.
 */
export async function handleDyScopeResult(result: DyScopeResult): Promise<void> {
  debugLog("handleDyScopeResult", {
    scope: result.scope,
    status: result.status,
    items_count: result.items.length,
    scope_count: result.scope_count,
    debug: result.debug,
  });
  if (!progress || result.task_id !== progress.task_id) return;
  // Reject results from outside the current scope (defensive; the
  // content script should only emit for the scope we asked it to).
  const expectedScope = progress.scopes[progress.current_scope_idx];
  if (result.scope !== expectedScope) return;

  progress.accumulated_counts[result.scope] = result.scope_count;

  // Post the per-scope items as a partial so the backend's
  // dy_bootstrap_videos_to_events helper propagates them through
  // memory before we move on. Mirrors the wire shape that
  // test_api_dy_ingest.py exercises end-to-end.
  await postTaskResult({
    task_id: progress.task_id,
    status: "partial",
    videos: result.items,
    scope_counts: { ...progress.accumulated_counts },
    debug: {
      scope: result.scope,
      scope_status: result.status,
      ...(result.debug ?? {}),
    },
  });

  progress.current_scope_idx += 1;
  if (progress.current_scope_idx < progress.scopes.length) {
    navigateToCurrentScope();
    return;
  }

  // All scopes done — finalise.
  await postTaskResult({
    task_id: progress.task_id,
    status: "ok",
    videos: [],
    scope_counts: { ...progress.accumulated_counts },
  });
  cleanupTask();
}

export async function handleDySearchResult(result: DySearchResult): Promise<void> {
  if (!searchProgress || result.task_id !== searchProgress.task_id) return;
  const expectedKeyword = searchProgress.keywords[searchProgress.current_keyword_idx];
  if (result.keyword !== expectedKeyword) return;

  if (result.status === "failed") {
    await postTaskResult({
      task_id: searchProgress.task_id,
      status: "failed",
      error: result.error || "search_failed",
      debug: result.debug,
    });
    cleanupTask();
    return;
  }

  searchProgress.accumulated_count += result.items.length;
  await postTaskResult({
    task_id: searchProgress.task_id,
    status: "partial",
    videos: result.items,
    scope_counts: { dy_search: searchProgress.accumulated_count },
    debug: {
      keyword: result.keyword,
      keyword_status: result.status,
      ...(result.debug ?? {}),
    },
  });

  searchProgress.current_keyword_idx += 1;
  if (searchProgress.current_keyword_idx < searchProgress.keywords.length) {
    navigateToCurrentSearch();
    return;
  }

  await postTaskResult({
    task_id: searchProgress.task_id,
    status: "ok",
    videos: [],
    scope_counts: { dy_search: searchProgress.accumulated_count },
  });
  cleanupTask();
}

export async function handleDyHotResult(result: DyHotResult): Promise<void> {
  if (!hotProgress || result.task_id !== hotProgress.task_id) return;
  const expected = hotProgress.hot_items[hotProgress.current_hot_idx];
  if (!expected || result.sentence_id !== expected.sentence_id) return;

  hotProgress.accumulated_count += result.items.length;
  await postTaskResult({
    task_id: hotProgress.task_id,
    status: "partial",
    videos: result.items,
    scope_counts: { dy_hot: hotProgress.accumulated_count },
    debug: {
      sentence_id: result.sentence_id,
      word: result.word,
      hot_status: result.status,
      ...(result.debug ?? {}),
      ...(result.error ? { error: result.error } : {}),
    },
  });

  if (
    shouldFinalizeHotTask({
      accumulatedCount: hotProgress.accumulated_count,
      maxItemsTotal: hotProgress.max_items_total,
      currentHotIndex: hotProgress.current_hot_idx,
      hotItemCount: hotProgress.hot_items.length,
    })
  ) {
    await postTaskResult({
      task_id: hotProgress.task_id,
      status: "ok",
      videos: [],
      scope_counts: { dy_hot: hotProgress.accumulated_count },
    });
    cleanupTask();
    return;
  }

  hotProgress.current_hot_idx += 1;
  if (hotProgress.current_hot_idx < hotProgress.hot_items.length) {
    navigateToCurrentHot();
    return;
  }

  await postTaskResult({
    task_id: hotProgress.task_id,
    status: "ok",
    videos: [],
    scope_counts: { dy_hot: hotProgress.accumulated_count },
  });
  cleanupTask();
}

export async function handleDyFeedResult(result: DyFeedResult): Promise<void> {
  if (!feedProgress || result.task_id !== feedProgress.task_id) return;

  if (result.status === "failed") {
    await postTaskResult({
      task_id: feedProgress.task_id,
      status: "failed",
      error: result.error || "feed_failed",
      debug: result.debug,
    });
    cleanupTask();
    return;
  }

  feedProgress.accumulated_count += result.items.length;
  await postTaskResult({
    task_id: feedProgress.task_id,
    status: "partial",
    videos: result.items,
    scope_counts: { dy_feed: feedProgress.accumulated_count },
    debug: {
      feed_status: result.status,
      ...(result.debug ?? {}),
    },
  });

  await postTaskResult({
    task_id: feedProgress.task_id,
    status: "ok",
    videos: [],
    scope_counts: { dy_feed: feedProgress.accumulated_count },
  });
  cleanupTask();
}

/**
 * Legacy single-shot result handler — retained so the existing
 * service-worker.ts DY_TASK_RESULT branch keeps working for any
 * caller that posts a final result directly (e.g. tests / future
 * non-bootstrap task types). Bootstrap now flows through
 * handleDyScopeResult instead.
 */
export async function handleTaskResult(result: DyTaskResult): Promise<void> {
  if (!currentTask || result.task_id !== currentTask.id) return;
  await postTaskResult(result);
  if (result.status === "partial") return;
  cleanupTask();
}

// Per-scope wire type — content script → dispatcher.
export interface DyScopeResult {
  task_id: string;
  scope: DouyinScope;
  items: DouyinBootstrapItem[];
  scope_count: number;
  status: "ok" | "empty" | "failed";
  error?: string;
  debug?: Record<string, unknown>;
}

export interface DySearchResult {
  task_id: string;
  keyword: string;
  items: DouyinSearchItem[];
  scope_count: number;
  status: "ok" | "empty" | "failed";
  error?: string;
  debug?: Record<string, unknown>;
}

export interface DyHotResult {
  task_id: string;
  sentence_id: string;
  word: string;
  items: DouyinSearchItem[];
  scope_count: number;
  status: "ok" | "empty" | "failed";
  error?: string;
  debug?: Record<string, unknown>;
}

export interface DyFeedResult {
  task_id: string;
  items: DouyinSearchItem[];
  scope_count: number;
  status: "ok" | "empty" | "failed";
  error?: string;
  debug?: Record<string, unknown>;
}

async function pollNextTask(): Promise<void> {
  if (taskInFlight) return;
  const task = await fetchNextTask();
  if (!task) return;
  await executeTask(task);
}

/**
 * Set up the dy task-poll alarm. Idempotent — chrome.alarms.create
 * with an existing name overwrites the schedule. Skip in non-extension
 * environments (node:test importing the module for pure-helper tests).
 *
 * Service-worker.ts owns the global ``chrome.alarms.onAlarm``
 * listener and dispatches into ``handleDyTaskAlarm`` from there,
 * mirroring the XHS pattern. Don't register a second listener here —
 * the result would be a torrent of redundant pollNextTask invocations.
 */
export function startDyTaskPolling(): void {
  if (_isSafari()) return;
  if (typeof chrome === "undefined" || !chrome.alarms) return;
  chrome.alarms.create(POLL_ALARM_NAME, {
    periodInMinutes: DEFAULT_POLL_INTERVAL_MS / 60_000,
  });
}

/**
 * Service-worker.ts's chrome.alarms.onAlarm dispatcher routes every
 * fired alarm through this. We only act on our own alarm name; other
 * alarms (xhs poll, cookie sync, event flush) are handled by their
 * respective modules.
 */
export function handleDyTaskAlarm(alarmName: string): void {
  if (alarmName === POLL_ALARM_NAME) {
    void pollNextTask();
  }
}

/**
 * Trigger an immediate poll. Used by the runtime-stream WebSocket
 * handler when the backend broadcasts ``dy_task_available``, so a
 * freshly-enqueued bootstrap task is picked up in <100ms instead of
 * the 0–60s next-alarm wait. Idempotent: pollNextTask() short-circuits
 * if a task is already in flight.
 */
export function pollDyTaskNow(): void {
  void pollNextTask();
}

/**
 * Public message handlers — service-worker.ts routes runtime messages
 * into these:
 *   - ``DY_TASK_RESULT``    → ``handleDyTaskResult`` (legacy single-shot)
 *   - ``DY_SCOPE_RESULT``   → ``handleDyScopeResult`` (per-scope; the
 *                              path bootstrap_profile actually uses)
 *
 * Renamed exports avoid colliding with the XHS module's same-named
 * symbols. The XHS task-dispatcher and the dy-task-dispatcher both
 * share the same chrome.runtime.onMessage listener in service-worker.ts;
 * each branch only acts on its own message types so they don't
 * interfere.
 */
export const handleDyTaskResult = handleTaskResult;
export const handleDySearchTaskResult = handleDySearchResult;
export const handleDyHotTaskResult = handleDyHotResult;
export const handleDyFeedTaskResult = handleDyFeedResult;
