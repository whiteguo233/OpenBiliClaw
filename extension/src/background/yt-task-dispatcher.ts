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

import type { YtBootstrapItem, YtScope, YtScopeResult } from "../content/yt/task-executor.js";
import { YT_SCOPE_URLS } from "../content/yt/task-executor.js";

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

const NEXT_TASK_URL = "http://127.0.0.1:8420/api/sources/yt/next-task";
const TASK_RESULT_URL = "http://127.0.0.1:8420/api/sources/yt/task-result";
const DEFAULT_POLL_INTERVAL_MS = 60_000;
const POLL_ALARM_NAME = "openbiliclaw-yt-task-poll";

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

export interface YtTask {
  id: string;
  type: "bootstrap_profile";
  scopes?: YtScope[];
  max_items_per_scope?: number;
  max_scroll_rounds?: number;
}

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

export function computeYtTaskTimeoutMs(task: YtTask): number {
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
let currentTask: YtTask | null = null;

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
    const resp = await fetch(NEXT_TASK_URL);
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
    await fetch(TASK_RESULT_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch {
    // Backend transient unavailability — drop rather than crash.
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
      chrome.tabs.remove(taskTabId);
    } catch {
      // Tab may already be closed.
    }
  }
  taskTabId = null;
  currentTask = null;
  progress = null;
  taskInFlight = false;
  releaseDispatcherMutex("yt");
}

function armTaskTimeout(task: YtTask): void {
  const ms = computeYtTaskTimeoutMs(task);
  taskTimeoutId = setTimeout(async () => {
    await postTaskResult({ task_id: task.id, status: "failed", error: "task_timeout" });
    cleanupTask();
  }, ms);
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
  const url = YT_SCOPE_URLS[scope];
  chrome.tabs.update(taskTabId, { url }, () => {
    onTabReady(taskTabId!, sendScopeExecuteMessage, { fallbackMs: 10_000 });
  });
}

// ---------------------------------------------------------------------------
// Main task executor
// ---------------------------------------------------------------------------

export async function executeTask(task: YtTask): Promise<void> {
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

  const firstUrl = YT_SCOPE_URLS[scopes[0]];
  let tab: chrome.tabs.Tab;
  try {
    tab = await chrome.tabs.create({ url: firstUrl, active: true });
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

  armTaskTimeout(task);
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

async function pollNextTask(): Promise<void> {
  if (taskInFlight) return;
  const task = await fetchNextTask();
  if (!task) return;
  await executeTask(task);
}

export function startYtTaskPolling(): void {
  if (typeof chrome === "undefined" || !chrome.alarms) return;
  chrome.alarms.create(POLL_ALARM_NAME, { periodInMinutes: DEFAULT_POLL_INTERVAL_MS / 60_000 });
}

export function handleYtTaskAlarm(alarmName: string): void {
  if (alarmName === POLL_ALARM_NAME) {
    void pollNextTask();
  }
}

export function pollYtTaskNow(): void {
  void pollNextTask();
}
