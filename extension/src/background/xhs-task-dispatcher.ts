/**
 * xhs task dispatcher — background polling for search / creator tasks.
 *
 * Polls ``GET /api/sources/xhs/next-task`` at intervals. When the backend
 * hands out a task, the dispatcher:
 *   1. Opens discovery URLs in a hidden tab. Search results come from the
 *      page's own API-response bridge when XHS skips hidden-tab DOM rendering.
 *      Profile bootstrap remains foreground because it intentionally scrolls.
 *   2. Listens for ``XHS_TASK_RESULT`` from the content script.
 *   3. POSTs the result back to ``/api/sources/xhs/task-result``.
 *   4. Closes the tab.
 *   5. Polls locally on a 45 s alarm; the backend persistently enforces the
 *      configured ``task_interval_seconds`` before handing out another
 *      search/creator task.
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

import { apiUrl } from "../shared/backend-endpoint.ts";
import { authenticatedFetch } from "../shared/auth.ts";
import { isNativeSaveTask, type NativeSaveResult, type NativeSaveTask } from "../shared/native-save.ts";
import { withTaskTabMarker } from "../shared/task-tab.ts";
import { ensureNativeSaveTaskRecovery, runNativeSaveTask } from "./native-save-task-runner.ts";
import { createTaskTab } from "./task-tab.ts";

const XHS_TASK_MARKER = "openbiliclaw_xhs_task";

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

export interface XhsLegacyTask {
  id: string;
  type: "search" | "creator" | "bootstrap_profile";
  keyword?: string;
  creator_url?: string;
  scopes?: XhsBootstrapScope[];
  max_items_per_scope?: number;
  max_scroll_rounds?: number;
  scroll_wait_ms?: number;
  max_stagnant_scroll_rounds?: number;
  /** Backend sets this when the xhs date preference is not "all". */
  need_published_at?: boolean;
}

export type XhsTask = XhsLegacyTask | NativeSaveTask;

export interface XhsTaskResult {
  task_id: string;
  urls: string[];
  notes?: unknown[];
  scope_counts?: Record<string, number>;
  status: "ok" | "empty" | "partial" | "error" | "rate_limited";
  error?: string;
  next_url?: string;
  debug?: Record<string, unknown>;
}

let taskInFlight = false;
let pollInFlight: Promise<void> | null = null;
let taskTabId: number | null = null;
let ownsTaskTab = false;
let taskTimeoutId: ReturnType<typeof setTimeout> | null = null;
let currentTaskId: string | null = null;
let currentTask: XhsTask | null = null;
let bootstrapNavigationCount = 0;
let bootstrapDebugSteps: unknown[] = [];
let dispatcherDebugEvents: unknown[] = [];
let taskUpdateListener: ((tabId: number, changeInfo: { status?: string }) => void) | null = null;
let taskNavigationFallbackId: ReturnType<typeof setTimeout> | null = null;

// ---------------------------------------------------------------------------
// Pure helpers (testable without chrome)
// ---------------------------------------------------------------------------

export function buildTaskUrl(task: XhsTask): string | null {
  if (task.type === "native_save") return task.content_url;
  if (task.type === "search" && task.keyword) {
    return withTaskTabMarker(
      `https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(task.keyword)}`,
      XHS_TASK_MARKER,
    );
  }
  if (task.type === "creator" && task.creator_url) {
    return withTaskTabMarker(task.creator_url, XHS_TASK_MARKER);
  }
  if (task.type === "bootstrap_profile") {
    return withTaskTabMarker("https://www.xiaohongshu.com/explore", XHS_TASK_MARKER);
  }
  return null;
}

export function isValidTask(task: unknown): task is XhsTask {
  if (isNativeSaveTask(task)) {
    return task.platform === "xiaohongshu" && task.platform_slug === "xhs";
  }
  if (typeof task !== "object" || task === null) return false;
  const t = task as Record<string, unknown>;
  if (typeof t.id !== "string" || !t.id) return false;
  if (t.type !== "search" && t.type !== "creator" && t.type !== "bootstrap_profile") {
    return false;
  }
  return true;
}

function bootstrapScrollableScopeCount(task: XhsLegacyTask): number {
  const scopes =
    Array.isArray(task.scopes) && task.scopes.length > 0
      ? task.scopes
      : (["saved", "liked"] as XhsBootstrapScope[]);
  const count = scopes.filter((scope) => scope === "saved" || scope === "liked").length;
  return Math.max(1, count);
}

export function computeTaskTimeoutMs(task: XhsLegacyTask): number {
  if (task.type !== "bootstrap_profile") return TASK_TIMEOUT_MS;
  const rounds =
    typeof task.max_scroll_rounds === "number" && Number.isFinite(task.max_scroll_rounds)
      ? Math.max(0, Math.floor(task.max_scroll_rounds))
      : 0;
  const roundBudget = rounds * bootstrapScrollableScopeCount(task);
  if (typeof task.scroll_wait_ms === "number" && Number.isFinite(task.scroll_wait_ms)) {
    const scrollWaitMs = Math.min(
      Math.max(Math.floor(task.scroll_wait_ms), MIN_BOOTSTRAP_SCROLL_WAIT_MS),
      MAX_BOOTSTRAP_SCROLL_WAIT_MS,
    );
    return Math.min(
      Math.max(TASK_TIMEOUT_MS, TASK_TIMEOUT_MS + roundBudget * (scrollWaitMs + 500) * 2),
      BOOTSTRAP_MAX_EXTENDED_TASK_TIMEOUT_MS,
    );
  }
  return Math.min(
    Math.max(
      TASK_TIMEOUT_MS,
      TASK_TIMEOUT_MS + roundBudget * BOOTSTRAP_SCROLL_TIMEOUT_PER_ROUND_MS,
    ),
    BOOTSTRAP_MAX_TASK_TIMEOUT_MS,
  );
}

function shouldActivateBeforeExecute(task: XhsLegacyTask): boolean {
  // Init-time bootstrap runs in a foreground tab so the user can see
  // their profile being pulled (transparency) and so XHS's lazy-load
  // / scroll virtualization actually fires (it pauses for inactive
  // tabs). Discovery tasks (search / creator) stay in background to
  // avoid disrupting active browsing.
  if (task.type !== "bootstrap_profile") return false;
  return bootstrapNavigationCount > 0;
}

function shouldOpenTaskForeground(task: XhsLegacyTask): boolean {
  return task.type === "bootstrap_profile";
}

function buildExecuteMessageData(task: XhsLegacyTask): Record<string, unknown> {
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
  if (task.need_published_at !== undefined) {
    data.need_published_at = task.need_published_at;
  }
  return data;
}

function isScrollableBootstrapTask(task: XhsLegacyTask): boolean {
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

function recordDispatcherDebug(event: string, data: Record<string, unknown> = {}): void {
  dispatcherDebugEvents.push({
    event,
    at_ms: Date.now(),
    task_id: currentTaskId,
    navigation_count: bootstrapNavigationCount,
    ...data,
  });
  if (dispatcherDebugEvents.length > 40) {
    dispatcherDebugEvents = dispatcherDebugEvents.slice(-40);
  }
}

function buildTimeoutDebug(task: XhsLegacyTask): Record<string, unknown> {
  const debug: Record<string, unknown> = {
    xhs_dispatcher: {
      reason: "timeout",
      timeout_ms: computeTaskTimeoutMs(task),
      events: dispatcherDebugEvents,
    },
  };
  if (bootstrapDebugSteps.length > 0) {
    debug.xhs_bootstrap = { steps: bootstrapDebugSteps };
  }
  return debug;
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
    const response = await authenticatedFetch(await apiUrl("/sources/xhs/next-task"), {
      method: "GET",
    });
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
    await authenticatedFetch(await apiUrl("/sources/xhs/task-result"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch {
    // Best-effort — log but don't crash.
  }
}

export interface XhsNativeSaveResultTransport {
  resolveUrl: (path: string) => Promise<string>;
  fetch: (input: string, init: RequestInit) => Promise<unknown>;
}

const XHS_NATIVE_SAVE_RESULT_TRANSPORT: XhsNativeSaveResultTransport = {
  resolveUrl: apiUrl,
  fetch: authenticatedFetch,
};

export async function postXhsNativeSaveResult(
  result: NativeSaveResult,
  transport: XhsNativeSaveResultTransport = XHS_NATIVE_SAVE_RESULT_TRANSPORT,
): Promise<void> {
  try {
    await transport.fetch(await transport.resolveUrl("/sources/xhs/task-result"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch {
    // Backend transient unavailability should not crash the service worker.
  }
}

export interface XhsNativeSaveDispatchDependencies {
  run: (
    task: NativeSaveTask,
    platformSlug: "xhs",
    postResult: (result: NativeSaveResult) => Promise<void>,
  ) => Promise<void>;
  postResult: (result: NativeSaveResult) => Promise<void>;
}

/** Behavior seam used by executeTask and tests to keep native-result closure explicit. */
export async function dispatchXhsNativeSaveTask(
  task: NativeSaveTask,
  dependencies: XhsNativeSaveDispatchDependencies,
): Promise<void> {
  await dependencies.run(task, "xhs", dependencies.postResult);
}

const XHS_NATIVE_SAVE_DISPATCH_DEPENDENCIES: XhsNativeSaveDispatchDependencies = {
  run: runNativeSaveTask,
  postResult: postXhsNativeSaveResult,
};

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
  const tabToClose = taskTabId !== null && ownsTaskTab ? taskTabId : null;
  taskTabId = null;
  ownsTaskTab = false;
  currentTaskId = null;
  currentTask = null;
  bootstrapNavigationCount = 0;
  bootstrapDebugSteps = [];
  dispatcherDebugEvents = [];
  taskInFlight = false;
  releaseDispatcherMutex("xhs");
  if (tabToClose !== null) {
    void (async () => {
      await chrome.tabs.remove(tabToClose).catch(() => {});
    })();
  }
}

function armTaskTimeout(task: XhsLegacyTask): void {
  if (taskTimeoutId !== null) {
    clearTimeout(taskTimeoutId);
    taskTimeoutId = null;
  }
  taskTimeoutId = setTimeout(() => {
    if (currentTaskId === task.id) {
      recordDispatcherDebug("timeout_fired", { timeout_ms: computeTaskTimeoutMs(task) });
      void reportTaskResult({
        task_id: task.id,
        urls: [],
        status: "error",
        error: "timeout",
        debug: buildTimeoutDebug(task),
      });
      cleanupTask();
    }
  }, computeTaskTimeoutMs(task));
}

async function sendExecuteMessageToTab(tabId: number, task: XhsLegacyTask): Promise<void> {
  if (shouldActivateBeforeExecute(task)) {
    recordDispatcherDebug("activate_tab_before_execute", { tab_id: tabId });
    await chrome.tabs.update(tabId, { active: true });
  }
  recordDispatcherDebug("send_execute_message", {
    tab_id: tabId,
    page: buildTaskUrl(task) ?? "",
  });
  await chrome.tabs.sendMessage(tabId, {
    action: "XHS_TASK_EXECUTE",
    data: buildExecuteMessageData(task),
  });
  recordDispatcherDebug("send_execute_message_done", { tab_id: tabId });
}

function handleExecuteMessageFailure(task: XhsLegacyTask): void {
  if (currentTaskId !== task.id) return;
  recordDispatcherDebug("send_execute_message_failed");
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

function armClickedNavigationFallback(task: XhsLegacyTask, tabId: number): void {
  clearNavigationFallback();
  taskNavigationFallbackId = setTimeout(() => {
    taskNavigationFallbackId = null;
    if (currentTaskId !== task.id || taskTabId !== tabId) return;
    if (taskUpdateListener !== null) {
      chrome.tabs.onUpdated.removeListener(taskUpdateListener);
      taskUpdateListener = null;
    }
    recordDispatcherDebug("clicked_navigation_fallback_send", { tab_id: tabId });
    void sendExecuteMessageToTab(tabId, task).catch(() => handleExecuteMessageFailure(task));
  }, BOOTSTRAP_CLICKED_NAVIGATION_FALLBACK_MS);
}

function armTaskLoadListener(task: XhsLegacyTask): void {
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
    recordDispatcherDebug("tab_load_complete", { tab_id: updatedTabId });
    void sendExecuteMessageToTab(updatedTabId, task).catch(() =>
      handleExecuteMessageFailure(task),
    );
  };
  taskUpdateListener = listener;
  chrome.tabs.onUpdated.addListener(listener);
}

export async function executeTask(
  task: XhsTask,
  nativeDependencies: XhsNativeSaveDispatchDependencies = XHS_NATIVE_SAVE_DISPATCH_DEPENDENCIES,
  mutexAlreadyHeld = false,
): Promise<void> {
  if (task.type === "native_save") {
    if (taskInFlight) return;
    taskInFlight = true;
    try {
      await dispatchXhsNativeSaveTask(task, nativeDependencies);
    } finally {
      taskInFlight = false;
    }
    return;
  }
  if (taskInFlight) return;
  // Direct callers acquire here. pollXhsTaskOnce acquires before claiming
  // from the backend so a busy Douyin dispatcher cannot strand an already
  // claimed XHS task in ``in_progress``.
  if (!mutexAlreadyHeld && !tryAcquireDispatcherMutex("xhs")) return;
  taskInFlight = true;
  currentTaskId = task.id;
  currentTask = task;
  recordDispatcherDebug("task_started", { timeout_ms: computeTaskTimeoutMs(task) });

  const url = buildTaskUrl(task);
  if (!url) {
    await reportTaskResult({ task_id: task.id, urls: [], status: "error", error: "no_url" });
    cleanupTask();
    return;
  }

  try {
    const foreground = shouldOpenTaskForeground(task);
    const tab = await createTaskTab({
      url,
      active: foreground,
    });
    taskTabId = tab.id ?? null;
    ownsTaskTab = taskTabId !== null;
    recordDispatcherDebug("task_tab_created", {
      tab_id: taskTabId ?? "",
      url,
      active: foreground,
    });
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
  recordDispatcherDebug("task_result_received", {
    status: result.status,
    has_next_url: Boolean(result.next_url),
    url_count: result.urls.length,
    note_count: Array.isArray(result.notes) ? result.notes.length : 0,
  });
  if (currentTask?.type === "bootstrap_profile" && result.status === "partial") {
    bootstrapDebugSteps.push(...extractBootstrapDebugSteps(result.debug));
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
    chrome.tabs.update(tabId, { url: withTaskTabMarker(result.next_url, XHS_TASK_MARKER) }).catch(() => {
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

export function pollXhsTaskOnce(): Promise<void> {
  if (pollInFlight) return pollInFlight;
  const running = (async () => {
    await ensureNativeSaveTaskRecovery();
    if (taskInFlight) return;
    // Acquire the cross-source mutex before GET /next-task. That endpoint
    // atomically changes a task from pending to in_progress, so claiming first
    // and discovering a busy Douyin dispatcher afterwards would permanently
    // strand the task without ever opening a tab or posting a result.
    if (!tryAcquireDispatcherMutex("xhs")) return;
    let releaseOnExit = true;
    try {
      const task = await fetchNextTask();
      if (!task) return;
      if (task.type === "native_save") {
        // Native-save has its own durable runner/recovery path and historically
        // does not hold the legacy discovery mutex while it executes.
        releaseDispatcherMutex("xhs");
        releaseOnExit = false;
        await executeTask(task);
        return;
      }
      await executeTask(task, XHS_NATIVE_SAVE_DISPATCH_DEPENDENCIES, true);
      // The legacy task lifecycle now owns the mutex; cleanupTask releases it
      // after a result, timeout, or tab/message failure.
      releaseOnExit = false;
    } finally {
      if (releaseOnExit) releaseDispatcherMutex("xhs");
    }
  })();
  pollInFlight = running;
  void running.finally(() => {
    if (pollInFlight === running) pollInFlight = null;
  });
  return running;
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
  void pollXhsTaskOnce();
}

/**
 * Trigger an immediate poll. Used by the runtime-stream WebSocket
 * handler when the backend broadcasts ``xhs_task_available``, so a
 * freshly-enqueued bootstrap task is picked up in <100ms instead of
 * the 0–60s next-alarm wait. Idempotent: pollOnce() short-circuits
 * if a task is already in flight.
 */
export function pollXhsTaskNow(): void {
  void pollXhsTaskOnce();
}
