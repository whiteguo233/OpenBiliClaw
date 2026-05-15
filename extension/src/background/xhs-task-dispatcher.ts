/**
 * xhs task dispatcher — background polling for search / creator tasks.
 *
 * Polls ``GET /api/sources/xhs/next-task`` at intervals. When the backend
 * hands out a task, the dispatcher:
 *   1. Opens a background tab at the appropriate xhs URL.
 *   2. Listens for ``XHS_TASK_RESULT`` from the content script.
 *   3. POSTs the result back to ``/api/sources/xhs/task-result``.
 *   4. Closes the tab.
 *   5. Waits ``task_interval_seconds`` before asking for the next task.
 *
 * Only one task is in flight at a time (mutex). A hard 30s timeout per
 * task protects against hung pages. Cross-source mutex (see
 * ``dispatcher-mutex.ts``) ensures long-running task tabs do not race
 * each other when daemon producers fire while the user runs a manual
 * fetch command.
 */

// Cross-source mutex via globalThis. Both XHS and DY dispatchers
// inline the same helper and write/read the same fields on
// globalThis, so they coordinate without needing to import a
// shared module — sidesteps the node:test ESM-resolver issue with
// .js→.ts paths. See dispatcher-mutex.ts for the rationale and
// the canonical Single-File reference (kept as documentation, not
// an actual import target).
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

const NEXT_TASK_URL = "http://127.0.0.1:8420/api/sources/xhs/next-task";
const TASK_RESULT_URL = "http://127.0.0.1:8420/api/sources/xhs/task-result";
const DEFAULT_POLL_INTERVAL_MS = 45_000;
const TASK_TIMEOUT_MS = 30_000;
const BOOTSTRAP_SCROLL_TIMEOUT_PER_ROUND_MS = 3_000;
const BOOTSTRAP_MAX_TASK_TIMEOUT_MS = 180_000;
const BOOTSTRAP_MAX_EXTENDED_TASK_TIMEOUT_MS = 360_000;
const MIN_BOOTSTRAP_SCROLL_WAIT_MS = 500;
const MAX_BOOTSTRAP_SCROLL_WAIT_MS = 5_000;
const BOOTSTRAP_CLICKED_NAVIGATION_FALLBACK_MS = 2_500;
const POLL_ALARM_NAME = "openbiliclaw-xhs-task-poll";

export type XhsBootstrapScope = "saved" | "liked" | "xhs_history";

export interface XhsTask {
  id: string;
  type: "search" | "creator" | "bootstrap_profile";
  keyword?: string;
  creator_url?: string;
  scopes?: XhsBootstrapScope[];
  max_items_per_scope?: number;
  max_scroll_rounds?: number;
  scroll_wait_ms?: number;
  max_stagnant_scroll_rounds?: number;
}

export interface XhsTaskResult {
  task_id: string;
  urls: string[];
  notes?: unknown[];
  scope_counts?: Record<string, number>;
  status: "ok" | "empty" | "partial" | "error";
  error?: string;
  next_url?: string;
  debug?: Record<string, unknown>;
}

let taskInFlight = false;
let taskTabId: number | null = null;
let ownsTaskTab = false;
let taskTimeoutId: ReturnType<typeof setTimeout> | null = null;
let currentTaskId: string | null = null;
let currentTask: XhsTask | null = null;
let bootstrapNavigationCount = 0;
let bootstrapDebugSteps: unknown[] = [];
let taskUpdateListener: ((tabId: number, changeInfo: { status?: string }) => void) | null = null;
let taskNavigationFallbackId: ReturnType<typeof setTimeout> | null = null;

// ---------------------------------------------------------------------------
// Pure helpers (testable without chrome)
// ---------------------------------------------------------------------------

export function buildTaskUrl(task: XhsTask): string | null {
  if (task.type === "search" && task.keyword) {
    return `https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(task.keyword)}`;
  }
  if (task.type === "creator" && task.creator_url) {
    return task.creator_url;
  }
  if (task.type === "bootstrap_profile") {
    return "https://www.xiaohongshu.com/explore";
  }
  return null;
}

export function isValidTask(task: unknown): task is XhsTask {
  if (typeof task !== "object" || task === null) return false;
  const t = task as Record<string, unknown>;
  if (typeof t.id !== "string" || !t.id) return false;
  if (t.type !== "search" && t.type !== "creator" && t.type !== "bootstrap_profile") {
    return false;
  }
  return true;
}

export function computeTaskTimeoutMs(task: XhsTask): number {
  if (task.type !== "bootstrap_profile") return TASK_TIMEOUT_MS;
  const rounds =
    typeof task.max_scroll_rounds === "number" && Number.isFinite(task.max_scroll_rounds)
      ? Math.max(0, Math.floor(task.max_scroll_rounds))
      : 0;
  if (typeof task.scroll_wait_ms === "number" && Number.isFinite(task.scroll_wait_ms)) {
    const scrollWaitMs = Math.min(
      Math.max(Math.floor(task.scroll_wait_ms), MIN_BOOTSTRAP_SCROLL_WAIT_MS),
      MAX_BOOTSTRAP_SCROLL_WAIT_MS,
    );
    return Math.min(
      Math.max(TASK_TIMEOUT_MS, TASK_TIMEOUT_MS + rounds * (scrollWaitMs + 500) * 2),
      BOOTSTRAP_MAX_EXTENDED_TASK_TIMEOUT_MS,
    );
  }
  return Math.min(
    Math.max(TASK_TIMEOUT_MS, TASK_TIMEOUT_MS + rounds * BOOTSTRAP_SCROLL_TIMEOUT_PER_ROUND_MS),
    BOOTSTRAP_MAX_TASK_TIMEOUT_MS,
  );
}

function shouldActivateBeforeExecute(task: XhsTask): boolean {
  // Init-time bootstrap runs in a foreground tab so the user can see
  // their profile being pulled (transparency) and so XHS's lazy-load
  // / scroll virtualization actually fires (it pauses for inactive
  // tabs). Discovery tasks (search / creator) stay in background to
  // avoid disrupting active browsing.
  if (task.type !== "bootstrap_profile") return false;
  return bootstrapNavigationCount > 0;
}

function buildExecuteMessageData(task: XhsTask): Record<string, unknown> {
  const data: Record<string, unknown> = { task_id: task.id, type: task.type };
  if (task.scopes !== undefined) data.scopes = task.scopes;
  if (task.max_items_per_scope !== undefined) {
    data.max_items_per_scope = task.max_items_per_scope;
  }
  if (task.max_scroll_rounds !== undefined) data.max_scroll_rounds = task.max_scroll_rounds;
  if (task.scroll_wait_ms !== undefined) data.scroll_wait_ms = task.scroll_wait_ms;
  if (task.max_stagnant_scroll_rounds !== undefined) {
    data.max_stagnant_scroll_rounds = task.max_stagnant_scroll_rounds;
  }
  return data;
}

function isScrollableBootstrapTask(task: XhsTask): boolean {
  return (
    task.type === "bootstrap_profile" &&
    typeof task.max_scroll_rounds === "number" &&
    Number.isFinite(task.max_scroll_rounds) &&
    task.max_scroll_rounds > 0
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function extractBootstrapDebugSteps(debug: unknown): unknown[] {
  if (!isRecord(debug)) return [];
  const bootstrap = debug.xhs_bootstrap;
  if (!isRecord(bootstrap)) return [];
  const steps = bootstrap.steps;
  return Array.isArray(steps) ? steps : [];
}

function mergeBootstrapDebugIntoResult(result: XhsTaskResult): XhsTaskResult {
  const resultSteps = extractBootstrapDebugSteps(result.debug);
  const steps = [...bootstrapDebugSteps, ...resultSteps];
  if (steps.length === 0) return result;

  const debug = isRecord(result.debug) ? { ...result.debug } : {};
  const bootstrap = isRecord(debug.xhs_bootstrap) ? { ...debug.xhs_bootstrap } : {};
  bootstrap.steps = steps;
  debug.xhs_bootstrap = bootstrap;
  return { ...result, debug };
}

function bootstrapClickedNextUrl(result: XhsTaskResult): boolean {
  const steps = extractBootstrapDebugSteps(result.debug);
  const last = steps[steps.length - 1];
  return isRecord(last) && last.next_url_clicked === true;
}

// ---------------------------------------------------------------------------
// Chrome integration
// ---------------------------------------------------------------------------

async function fetchNextTask(): Promise<XhsTask | null> {
  try {
    const response = await fetch(NEXT_TASK_URL, { method: "GET" });
    if (response.status === 204) return null;
    if (!response.ok) return null;
    const payload = await response.json();
    return isValidTask(payload) ? payload : null;
  } catch {
    return null;
  }
}

async function reportTaskResult(result: XhsTaskResult): Promise<void> {
  try {
    await fetch(TASK_RESULT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch {
    // Best-effort — log but don't crash.
  }
}

function cleanupTask(): void {
  if (taskTimeoutId !== null) {
    clearTimeout(taskTimeoutId);
    taskTimeoutId = null;
  }
  if (taskUpdateListener !== null) {
    chrome.tabs.onUpdated.removeListener(taskUpdateListener);
    taskUpdateListener = null;
  }
  if (taskNavigationFallbackId !== null) {
    clearTimeout(taskNavigationFallbackId);
    taskNavigationFallbackId = null;
  }
  if (taskTabId !== null && ownsTaskTab) {
    void chrome.tabs.remove(taskTabId).catch(() => {});
  }
  taskTabId = null;
  ownsTaskTab = false;
  currentTaskId = null;
  currentTask = null;
  bootstrapNavigationCount = 0;
  bootstrapDebugSteps = [];
  taskInFlight = false;
  releaseDispatcherMutex("xhs");
}

function armTaskTimeout(task: XhsTask): void {
  if (taskTimeoutId !== null) {
    clearTimeout(taskTimeoutId);
    taskTimeoutId = null;
  }
  taskTimeoutId = setTimeout(() => {
    if (currentTaskId === task.id) {
      void reportTaskResult({ task_id: task.id, urls: [], status: "error", error: "timeout" });
      cleanupTask();
    }
  }, computeTaskTimeoutMs(task));
}

async function sendExecuteMessageToTab(tabId: number, task: XhsTask): Promise<void> {
  if (shouldActivateBeforeExecute(task)) {
    await chrome.tabs.update(tabId, { active: true });
  }
  await chrome.tabs.sendMessage(tabId, {
    action: "XHS_TASK_EXECUTE",
    data: buildExecuteMessageData(task),
  });
}

function handleExecuteMessageFailure(task: XhsTask): void {
  if (currentTaskId !== task.id) return;
  void reportTaskResult({
    task_id: task.id,
    urls: [],
    status: "error",
    error: "sendMessage_failed",
  });
  cleanupTask();
}

function clearNavigationFallback(): void {
  if (taskNavigationFallbackId !== null) {
    clearTimeout(taskNavigationFallbackId);
    taskNavigationFallbackId = null;
  }
}

function armClickedNavigationFallback(task: XhsTask, tabId: number): void {
  clearNavigationFallback();
  taskNavigationFallbackId = setTimeout(() => {
    taskNavigationFallbackId = null;
    if (currentTaskId !== task.id || taskTabId !== tabId) return;
    if (taskUpdateListener !== null) {
      chrome.tabs.onUpdated.removeListener(taskUpdateListener);
      taskUpdateListener = null;
    }
    void sendExecuteMessageToTab(tabId, task).catch(() => handleExecuteMessageFailure(task));
  }, BOOTSTRAP_CLICKED_NAVIGATION_FALLBACK_MS);
}

function armTaskLoadListener(task: XhsTask): void {
  if (taskUpdateListener !== null) {
    chrome.tabs.onUpdated.removeListener(taskUpdateListener);
    taskUpdateListener = null;
  }

  const listener = (updatedTabId: number, changeInfo: { status?: string }): void => {
    if (updatedTabId !== taskTabId || changeInfo.status !== "complete") return;
    if (currentTaskId !== task.id) return;
    // Detach immediately so intra-page navigations don't re-trigger the handshake.
    chrome.tabs.onUpdated.removeListener(listener);
    if (taskUpdateListener === listener) taskUpdateListener = null;
    clearNavigationFallback();
    void sendExecuteMessageToTab(updatedTabId, task).catch(() =>
      handleExecuteMessageFailure(task),
    );
  };
  taskUpdateListener = listener;
  chrome.tabs.onUpdated.addListener(listener);
}

export async function executeTask(task: XhsTask): Promise<void> {
  if (taskInFlight) return;
  // Cross-source mutex — bail if Douyin dispatcher is currently
  // running a task. The XHS task remains in the queue and the next
  // alarm tick (60s) retries. See dispatcher-mutex.ts for rationale.
  if (!tryAcquireDispatcherMutex("xhs")) return;
  taskInFlight = true;
  currentTaskId = task.id;
  currentTask = task;

  const url = buildTaskUrl(task);
  if (!url) {
    await reportTaskResult({ task_id: task.id, urls: [], status: "error", error: "no_url" });
    cleanupTask();
    return;
  }

  try {
    // Foreground for init-time bootstrap (user is running ``openbiliclaw
    // init`` and expects to see XHS profile pull happen — also XHS's
    // virtualised lists only paginate properly in an active tab).
    // Background for discovery (search / creator) so ongoing scraping
    // doesn't interrupt active browsing.
    const tab = await chrome.tabs.create({
      url,
      active: task.type === "bootstrap_profile",
    });
    taskTabId = tab.id ?? null;
    ownsTaskTab = taskTabId !== null;
  } catch {
    await reportTaskResult({ task_id: task.id, urls: [], status: "error", error: "tab_create_failed" });
    cleanupTask();
    return;
  }

  // Once the tab finishes loading, hand off to the content-script executor.
  // Without this handshake the executor's onMessage listener never fires and
  // every task eventually trips the 30 s hard timeout.
  armTaskLoadListener(task);
  armTaskTimeout(task);
}

export async function handleTaskResult(result: XhsTaskResult): Promise<void> {
  if (!taskInFlight || result.task_id !== currentTaskId) return;
  if (currentTask?.type === "bootstrap_profile" && result.status === "partial") {
    await reportTaskResult(result);
    return;
  }
  if (
    currentTask?.type === "bootstrap_profile" &&
    result.next_url &&
    taskTabId !== null &&
    bootstrapNavigationCount < 2
  ) {
    const task = currentTask;
    const tabId = taskTabId;
    const clickedNextUrl = bootstrapClickedNextUrl(result);
    bootstrapDebugSteps.push(...extractBootstrapDebugSteps(result.debug));
    bootstrapNavigationCount += 1;
    armTaskLoadListener(task);
    armTaskTimeout(task);
    if (clickedNextUrl) {
      armClickedNavigationFallback(task, tabId);
      return;
    }
    chrome.tabs.update(tabId, { url: result.next_url }).catch(() => {
      if (currentTaskId !== task.id) return;
      void reportTaskResult({
        task_id: task.id,
        urls: [],
        status: "error",
        error: "tab_update_failed",
      });
      cleanupTask();
    });
    return;
  }
  await reportTaskResult(mergeBootstrapDebugIntoResult(result));
  cleanupTask();
}

async function pollOnce(): Promise<void> {
  if (taskInFlight) return;
  const task = await fetchNextTask();
  if (!task) return;
  await executeTask(task);
}

// ---------------------------------------------------------------------------
// Alarm-driven polling
// ---------------------------------------------------------------------------

export function startXhsTaskPolling(intervalMs: number = DEFAULT_POLL_INTERVAL_MS): void {
  chrome.alarms.create(POLL_ALARM_NAME, {
    periodInMinutes: intervalMs / 60_000,
  });
}

export function handleXhsTaskAlarm(alarmName: string): void {
  if (alarmName !== POLL_ALARM_NAME) return;
  void pollOnce();
}

/**
 * Trigger an immediate poll. Used by the runtime-stream WebSocket
 * handler when the backend broadcasts ``xhs_task_available``, so a
 * freshly-enqueued bootstrap task is picked up in <100ms instead of
 * the 0–60s next-alarm wait. Idempotent: pollOnce() short-circuits
 * if a task is already in flight.
 */
export function pollXhsTaskNow(): void {
  void pollOnce();
}
