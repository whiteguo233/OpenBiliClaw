/**
 * YouTube task dispatcher — background polling for bootstrap_profile tasks.
 *
 * Polls `GET /api/sources/yt/next-task` at intervals. When a bootstrap task
 * arrives, the dispatcher:
 *   1. Opens a foreground tab at the first scope URL.
 *   2. Waits for the tab to load, sends `YT_SCOPE_EXECUTE` to the content script.
 *   3. Receives `YT_SCOPE_RESULT`, POSTs it to `/api/sources/yt/task-result`.
 *   4. Navigates the same tab to the next scope URL (chrome.tabs.update).
 *   5. Repeats until all scopes are done, then sends a final status=ok.
 *   6. Closes the tab and releases the mutex.
 *
 * Unlike the Douyin dispatcher, no MAIN-world fetch-tap injection is needed —
 * YouTube data is read from the DOM directly by the content script.
 * Unlike Douyin's click-driven SPA navigation, each scope lives at its own
 * URL so chrome.tabs.update is safe and clean.
 */

import type { YtBootstrapItem, YtScope, YtScopeResult } from "../content/yt/task-executor.ts";
import { YT_SCOPE_URLS } from "../content/yt/task-executor.ts";
import { apiUrl } from "../shared/backend-endpoint.ts";
import { authenticatedFetch } from "../shared/auth.ts";
import { isNativeSaveTask, type NativeSaveResult, type NativeSaveTask } from "../shared/native-save.ts";
import { withTaskTabMarker } from "../shared/task-tab.ts";
import { ensureNativeSaveTaskRecovery, runNativeSaveTask } from "./native-save-task-runner.ts";
import { createTaskTab } from "./task-tab.ts";

const YT_TASK_MARKER = "openbiliclaw_yt_task";

// Cross-source mutex — same field as xhs/dy dispatchers so all three
// cooperate on a single long-running task slot.
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

const DEFAULT_POLL_INTERVAL_MS = 60_000;
const POLL_ALARM_NAME = "openbiliclaw-yt-task-poll";
export const YT_TASK_TIMEOUT_ALARM_NAME = "openbiliclaw-yt-task-timeout";
export const YT_TASK_SESSION_KEY = "openbiliclaw-yt-active-task";

// Per-scope timeout: 30s base + 3s per scroll round per scope. Matches
// Douyin convention. Max cap at 360s for very large libraries.
const BASE_TIMEOUT_MS = 30_000;
const PER_ROUND_MS = 3_000;
const MAX_TIMEOUT_MS = 360_000;

const DEFAULT_SCOPES: readonly YtScope[] = [
  "yt_history",
  "yt_subscriptions",
  "yt_likes",
];

// ---------------------------------------------------------------------------
// Task types
// ---------------------------------------------------------------------------

export interface YtBootstrapTask {
  id: string;
  type: "bootstrap_profile";
  scopes?: YtScope[];
  max_items_per_scope?: number;
  max_scroll_rounds?: number;
}

export type YtTask = YtBootstrapTask | NativeSaveTask;

interface YtTaskPayload {
  task_id: string;
  status: "ok" | "partial" | "empty" | "failed";
  items?: YtBootstrapItem[];
  scope_counts?: Record<string, number>;
  error?: string;
  debug?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Pure helpers (testable without chrome)
// ---------------------------------------------------------------------------

export function isValidYtTask(task: unknown): task is YtTask {
  if (isNativeSaveTask(task)) {
    return task.platform === "youtube" && task.platform_slug === "yt";
  }
  if (typeof task !== "object" || task === null) return false;
  const t = task as Record<string, unknown>;
  if (typeof t.id !== "string" || !t.id) return false;
  if (t.type !== "bootstrap_profile") return false;
  if (t.scopes !== undefined) {
    if (!Array.isArray(t.scopes)) return false;
    for (const s of t.scopes) {
      if (!DEFAULT_SCOPES.includes(s as YtScope)) return false;
    }
  }
  return true;
}

export function computeYtTaskTimeoutMs(task: YtBootstrapTask): number {
  const scopeCount =
    Array.isArray(task.scopes) && task.scopes.length > 0 ? task.scopes.length : DEFAULT_SCOPES.length;
  const rounds =
    typeof task.max_scroll_rounds === "number" && Number.isFinite(task.max_scroll_rounds)
      ? Math.max(0, Math.floor(task.max_scroll_rounds))
      : 10;
  const scrollBudget = scopeCount * rounds * PER_ROUND_MS;
  return Math.min(Math.max(BASE_TIMEOUT_MS, BASE_TIMEOUT_MS + scrollBudget), MAX_TIMEOUT_MS);
}

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------

let taskInFlight = false;
let taskTabId: number | null = null;
let taskTimeoutId: ReturnType<typeof setTimeout> | null = null;
let currentTask: YtBootstrapTask | null = null;

interface TaskProgress {
  task_id: string;
  scopes: YtScope[];
  current_scope_idx: number;
  accumulated_counts: Record<string, number>;
  max_items_per_scope: number;
  max_scroll_rounds: number;
}

let progress: TaskProgress | null = null;

// ---------------------------------------------------------------------------
// Network helpers
// ---------------------------------------------------------------------------

async function fetchNextTask(): Promise<YtTask | null> {
  try {
    const resp = await authenticatedFetch(await apiUrl("/sources/yt/next-task"));
    if (resp.status === 204) return null;
    if (!resp.ok) return null;
    const payload: unknown = await resp.json();
    return isValidYtTask(payload) ? payload : null;
  } catch {
    return null;
  }
}

async function postTaskResult(result: YtTaskPayload): Promise<void> {
  try {
    await authenticatedFetch(await apiUrl("/sources/yt/task-result"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch {
    // Backend transient unavailability — drop rather than crash.
  }
}

export interface YtNativeSaveResultTransport {
  resolveUrl: (path: string) => Promise<string>;
  fetch: (input: string, init: RequestInit) => Promise<{ ok?: boolean }>;
}

const YT_NATIVE_SAVE_RESULT_TRANSPORT: YtNativeSaveResultTransport = {
  resolveUrl: apiUrl,
  fetch: authenticatedFetch,
};

export async function postYtNativeSaveResult(
  result: NativeSaveResult,
  signal?: AbortSignal,
  transport: YtNativeSaveResultTransport = YT_NATIVE_SAVE_RESULT_TRANSPORT,
): Promise<void> {
  const response = await transport.fetch(await transport.resolveUrl("/sources/yt/task-result"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(result),
    signal,
  });
  if (response.ok !== true) throw new Error("YouTube native-save result was not acknowledged");
}

// ---------------------------------------------------------------------------
// Durable timeout state (survives MV3 service-worker sleep)
// ---------------------------------------------------------------------------

interface YtActiveTaskSession {
  task_id: string;
  deadline_at: number;
  tab_id: number | null;
}

function ytTaskStorageArea(): chrome.storage.StorageArea | null {
  if (typeof chrome === "undefined" || !chrome.storage) return null;
  try {
    return chrome.storage?.session ?? chrome.storage?.local ?? null;
  } catch {
    return null;
  }
}

async function readYtActiveTaskSession(): Promise<YtActiveTaskSession | null> {
  const storage = ytTaskStorageArea();
  if (!storage) return null;
  try {
    const raw: unknown = (await storage.get(YT_TASK_SESSION_KEY))[YT_TASK_SESSION_KEY];
    if (typeof raw !== "object" || raw === null) return null;
    const record = raw as Partial<YtActiveTaskSession>;
    if (typeof record.task_id !== "string" || !record.task_id) return null;
    if (typeof record.deadline_at !== "number" || !Number.isFinite(record.deadline_at)) {
      return null;
    }
    return {
      task_id: record.task_id,
      deadline_at: record.deadline_at,
      tab_id: typeof record.tab_id === "number" ? record.tab_id : null,
    };
  } catch {
    return null;
  }
}

async function writeYtActiveTaskSession(record: YtActiveTaskSession): Promise<void> {
  const storage = ytTaskStorageArea();
  if (!storage) return;
  try {
    await storage.set({ [YT_TASK_SESSION_KEY]: record });
  } catch {
    // Session storage is best-effort; the backend lease is the authority.
  }
}

async function clearYtActiveTaskSession(): Promise<void> {
  const storage = ytTaskStorageArea();
  if (!storage) return;
  try {
    await storage.remove(YT_TASK_SESSION_KEY);
  } catch {
    // Best-effort cleanup.
  }
}

async function clearYtTaskTimeoutAlarm(): Promise<void> {
  if (typeof chrome === "undefined" || !chrome.alarms?.clear) return;
  try {
    await chrome.alarms.clear(YT_TASK_TIMEOUT_ALARM_NAME);
  } catch {
    // Best-effort cleanup.
  }
}

async function closeYtTaskTab(tabId: number | null): Promise<void> {
  if (tabId === null) return;
  if (typeof chrome === "undefined" || !chrome.tabs?.remove) return;
  try {
    await chrome.tabs.remove(tabId);
  } catch {
    // Tab may already be closed.
  }
}

// ---------------------------------------------------------------------------
// Tab lifecycle
// ---------------------------------------------------------------------------

function cleanupTask(): void {
  if (taskTimeoutId !== null) {
    clearTimeout(taskTimeoutId);
    taskTimeoutId = null;
  }
  if (taskTabId !== null) {
    try {
      void chrome.tabs.remove(taskTabId);
    } catch {
      // Tab may already be closed.
    }
  }
  taskTabId = null;
  currentTask = null;
  progress = null;
  taskInFlight = false;
  releaseDispatcherMutex("yt");
  void clearYtTaskTimeoutAlarm();
  void clearYtActiveTaskSession();
}

async function armTaskTimeout(task: YtBootstrapTask): Promise<void> {
  const ms = computeYtTaskTimeoutMs(task);
  const deadlineAt = Date.now() + ms;
  await writeYtActiveTaskSession({ task_id: task.id, deadline_at: deadlineAt, tab_id: taskTabId });
  if (typeof chrome === "undefined" || !chrome.alarms) {
    // Fallback for browsers / tests without chrome.alarms. MV3 builds take
    // the alarm path below because setTimeout dies when the worker sleeps.
    taskTimeoutId = setTimeout(() => {
      void settleYtTaskTimeout("task_timeout");
    }, ms);
    return;
  }
  try {
    chrome.alarms.create(YT_TASK_TIMEOUT_ALARM_NAME, { when: deadlineAt });
  } catch {
    // Fallback to an in-memory timer if alarm creation fails.
    taskTimeoutId = setTimeout(() => {
      void settleYtTaskTimeout("task_timeout");
    }, ms);
  }
}

/**
 * Post a terminal ``task_timeout`` / ``service_worker_restart`` failure for
 * the currently-claimed task and tear down local state. When the worker has
 * just restarted (no in-memory task), the durable session record supplies the
 * task id and tab id so the orphaned task tab can still be closed.
 */
async function settleYtTaskTimeout(error: "task_timeout" | "service_worker_restart"): Promise<void> {
  if (taskInFlight && currentTask !== null) {
    await postTaskResult({ task_id: currentTask.id, status: "failed", error });
    cleanupTask();
    return;
  }

  const record = await readYtActiveTaskSession();
  if (!record) return;
  await postTaskResult({ task_id: record.task_id, status: "failed", error });
  await clearYtActiveTaskSession();
  await clearYtTaskTimeoutAlarm();
  await closeYtTaskTab(record.tab_id);
  releaseDispatcherMutex("yt");
}

/**
 * Wait for `tabs.status === "complete"` then invoke callback. Cleans up
 * after first fire to prevent SPA re-triggers.
 */
export function onTabReady(
  tabId: number,
  callback: () => void,
  options: { fallbackMs?: number } = {},
): void {
  let completed = false;
  let fallbackTimer: ReturnType<typeof setTimeout> | null = null;

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

  const listener = (updatedId: number, info: { status?: string }): void => {
    if (updatedId !== tabId || info.status !== "complete") return;
    runOnce();
  };

  chrome.tabs.onUpdated.addListener(listener);

  if (typeof options.fallbackMs === "number" && Number.isFinite(options.fallbackMs)) {
    fallbackTimer = setTimeout(runOnce, options.fallbackMs);
  }

  void chrome.tabs
    .get(tabId)
    .then((tab) => {
      if (tab.status === "complete") runOnce();
    })
    .catch(() => {});
}

// ---------------------------------------------------------------------------
// Scope execution
// ---------------------------------------------------------------------------

function sendScopeExecuteMessage(): void {
  if (!progress || taskTabId === null) return;
  const scope = progress.scopes[progress.current_scope_idx];
  if (!scope) return;

  void chrome.tabs
    .sendMessage(taskTabId, {
      action: "YT_SCOPE_EXECUTE",
      data: {
        task_id: progress.task_id,
        scope,
        max_items_per_scope: progress.max_items_per_scope,
        max_scroll_rounds: progress.max_scroll_rounds,
      },
    })
    .catch(() => {
      // Content script not ready or wrong URL — synthesise empty result.
      void handleYtScopeResult({
        task_id: progress!.task_id,
        scope,
        items: [],
        scope_count: 0,
        status: "failed",
        error: "sendMessage_failed",
      });
    });
}

function navigateToCurrentScope(): void {
  if (!progress || taskTabId === null) return;
  const scope = progress.scopes[progress.current_scope_idx];
  if (!scope) return;
  const url = withTaskTabMarker(YT_SCOPE_URLS[scope], YT_TASK_MARKER);
  chrome.tabs.update(taskTabId, { url }, () => {
    onTabReady(taskTabId!, sendScopeExecuteMessage, { fallbackMs: 10_000 });
  });
}

// ---------------------------------------------------------------------------
// Main task executor
// ---------------------------------------------------------------------------

export async function executeTask(task: YtTask): Promise<void> {
  if (task.type === "native_save") {
    if (taskInFlight) return;
    taskInFlight = true;
    try {
      await runNativeSaveTask(task, "yt", postYtNativeSaveResult);
    } finally {
      taskInFlight = false;
    }
    return;
  }
  if (taskInFlight) return;
  if (!tryAcquireDispatcherMutex("yt")) return;

  taskInFlight = true;
  currentTask = task;

  const scopes: YtScope[] =
    task.scopes && task.scopes.length > 0 ? task.scopes : [...DEFAULT_SCOPES];

  progress = {
    task_id: task.id,
    scopes,
    current_scope_idx: 0,
    accumulated_counts: {},
    max_items_per_scope: task.max_items_per_scope ?? 300,
    max_scroll_rounds: task.max_scroll_rounds ?? 10,
  };

  const firstUrl = withTaskTabMarker(YT_SCOPE_URLS[scopes[0]], YT_TASK_MARKER);
  let tab: chrome.tabs.Tab;
  try {
    tab = await createTaskTab({ url: firstUrl, active: true });
  } catch {
    await postTaskResult({ task_id: task.id, status: "failed", error: "tab_create_failed" });
    cleanupTask();
    return;
  }

  taskTabId = tab.id ?? null;
  if (taskTabId === null) {
    await postTaskResult({ task_id: task.id, status: "failed", error: "tab_id_unknown" });
    cleanupTask();
    return;
  }

  await armTaskTimeout(task);
  onTabReady(taskTabId, sendScopeExecuteMessage, { fallbackMs: 12_000 });
}

// ---------------------------------------------------------------------------
// Result handler (called from service-worker message routing)
// ---------------------------------------------------------------------------

export async function handleYtScopeResult(result: YtScopeResult): Promise<void> {
  if (!progress || result.task_id !== progress.task_id) return;
  const expectedScope = progress.scopes[progress.current_scope_idx];
  if (result.scope !== expectedScope) return;

  progress.accumulated_counts[result.scope] = result.scope_count;

  // Post this scope's items as a partial so the backend propagates them
  // to memory incrementally (mirrors the Douyin per-scope pattern).
  await postTaskResult({
    task_id: progress.task_id,
    status: "partial",
    items: result.items,
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

  // All scopes done — send final status=ok with empty items list.
  await postTaskResult({
    task_id: progress.task_id,
    status: "ok",
    items: [],
    scope_counts: { ...progress.accumulated_counts },
  });
  cleanupTask();
}

// ---------------------------------------------------------------------------
// Polling & alarm wiring
// ---------------------------------------------------------------------------

/**
 * Recover a YouTube task whose service worker restarted mid-flight.
 *
 * The durable session record is only written after a task is claimed, and it
 * is cleared by every terminal path. If it still exists while this worker has
 * no in-memory task, the previous worker died (MV3 sleep / browser restart)
 * and can no longer drive the content-script state machine — settle the lease
 * now so the backend queue is not wedged until the stale-in-progress fallback.
 */
async function recoverInterruptedYtTask(): Promise<void> {
  if (taskInFlight) return;
  if (!(await readYtActiveTaskSession())) return;
  await settleYtTaskTimeout("service_worker_restart");
}

async function pollNextTask(): Promise<void> {
  await ensureNativeSaveTaskRecovery();
  await recoverInterruptedYtTask();
  if (taskInFlight) return;
  const task = await fetchNextTask();
  if (!task) return;
  await executeTask(task);
}

function pollNextTaskBestEffort(): void {
  void pollNextTask().catch(() => {
    // Result delivery is bounded and cleanup has already run; the next alarm can recover the task.
  });
}

export function startYtTaskPolling(): void {
  if (typeof chrome === "undefined" || !chrome.alarms) return;
  chrome.alarms.create(POLL_ALARM_NAME, { periodInMinutes: DEFAULT_POLL_INTERVAL_MS / 60_000 });
}

export function handleYtTaskAlarm(alarmName: string): void {
  if (alarmName === POLL_ALARM_NAME) {
    pollNextTaskBestEffort();
    return;
  }
  if (alarmName === YT_TASK_TIMEOUT_ALARM_NAME) {
    void settleYtTaskTimeout("task_timeout");
  }
}

export function pollYtTaskNow(): void {
  pollNextTaskBestEffort();
}
