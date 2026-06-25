/**
 * Bilibili extension-search task dispatcher.
 *
 * Polls the backend for fallback search tasks, opens a rendered Bilibili
 * search page in a background tab, asks the Bilibili content script to scrape
 * visible result cards, and posts the videos back to the backend.
 */

import { apiUrl } from "../shared/backend-endpoint.ts";

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

const DEFAULT_POLL_INTERVAL_MS = 45_000;
const TASK_TIMEOUT_MS = 90_000;
const POLL_ALARM_NAME = "openbiliclaw-bili-task-poll";
const TAB_READY_FALLBACK_MS = 10_000;
const CONTENT_SCRIPT_RETRY_INTERVAL_MS = 250;
const CONTENT_SCRIPT_READY_TIMEOUT_MS = 8_000;

export interface BiliTask {
  id: string;
  type: "search";
  query?: string;
  keyword?: string;
  limit?: number;
  page?: number;
  page_size?: number;
  source_keyword_id?: number;
}

export interface BiliTaskResult {
  task_id: string;
  status: "ok" | "empty" | "partial" | "failed";
  videos?: unknown[];
  error?: string;
  debug?: Record<string, unknown>;
}

let taskInFlight = false;
let taskTabId: number | null = null;
let taskTimeoutId: ReturnType<typeof setTimeout> | null = null;
let messageRetryTimeoutId: ReturnType<typeof setTimeout> | null = null;
let currentTaskId: string | null = null;
let taskUpdateListener: ((tabId: number, changeInfo: { status?: string }) => void) | null = null;

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

function taskQuery(task: BiliTask): string {
  return String(task.query ?? task.keyword ?? "").trim();
}

export function buildBiliTaskUrl(task: BiliTask): string | null {
  if (task.type !== "search") return null;
  const query = taskQuery(task);
  if (!query) return null;
  const params = [`keyword=${encodeURIComponent(query)}`];
  if (typeof task.page === "number" && Number.isFinite(task.page) && task.page > 1) {
    params.push(`page=${Math.floor(task.page)}`);
  }
  return `https://search.bilibili.com/all?${params.join("&")}`;
}

export function isValidBiliTask(task: unknown): task is BiliTask {
  if (typeof task !== "object" || task === null) return false;
  const t = task as Record<string, unknown>;
  if (typeof t.id !== "string" || !t.id.trim()) return false;
  if (t.type !== "search") return false;
  const query = typeof t.query === "string" ? t.query : typeof t.keyword === "string" ? t.keyword : "";
  if (!query.trim()) return false;
  for (const key of ["limit", "page", "page_size"] as const) {
    if (t[key] === undefined) continue;
    if (typeof t[key] !== "number" || !Number.isFinite(t[key]) || t[key] <= 0) return false;
  }
  return true;
}

export function computeBiliTaskTimeoutMs(_task: BiliTask): number {
  return TASK_TIMEOUT_MS;
}

export function buildBiliExecuteMessageData(task: BiliTask): Record<string, unknown> {
  const data: Record<string, unknown> = {
    task_id: task.id,
    type: task.type,
    query: taskQuery(task),
  };
  if (task.limit !== undefined) data.limit = task.limit;
  if (task.page_size !== undefined) data.page_size = task.page_size;
  return data;
}

// ---------------------------------------------------------------------------
// Backend API
// ---------------------------------------------------------------------------

async function fetchNextTask(): Promise<BiliTask | null> {
  try {
    const response = await fetch(await apiUrl("/sources/bili/next-task"), { method: "GET" });
    if (response.status === 204) return null;
    if (!response.ok) return null;
    const payload: unknown = await response.json();
    return isValidBiliTask(payload) ? payload : null;
  } catch {
    return null;
  }
}

async function postTaskResult(result: BiliTaskResult): Promise<void> {
  try {
    await fetch(await apiUrl("/sources/bili/task-result"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch {
    // Backend transient unavailability — keep the service worker alive.
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
  if (messageRetryTimeoutId !== null) {
    clearTimeout(messageRetryTimeoutId);
    messageRetryTimeoutId = null;
  }
  if (taskUpdateListener !== null) {
    chrome.tabs.onUpdated.removeListener(taskUpdateListener);
    taskUpdateListener = null;
  }
  if (taskTabId !== null) {
    void chrome.tabs.remove(taskTabId).catch(() => {});
  }
  taskTabId = null;
  currentTaskId = null;
  taskInFlight = false;
  releaseDispatcherMutex("bili");
}

function armTaskTimeout(task: BiliTask): void {
  taskTimeoutId = setTimeout(() => {
    if (currentTaskId !== task.id) return;
    void postTaskResult({
      task_id: task.id,
      status: "failed",
      error: "task_timeout",
      debug: { timeout_ms: computeBiliTaskTimeoutMs(task) },
    });
    cleanupTask();
  }, computeBiliTaskTimeoutMs(task));
}

function onTabReady(tabId: number, callback: () => void): void {
  let completed = false;
  let fallbackTimer: ReturnType<typeof setTimeout> | null = null;

  const runOnce = (): void => {
    if (completed) return;
    completed = true;
    if (fallbackTimer !== null) clearTimeout(fallbackTimer);
    chrome.tabs.onUpdated.removeListener(listener);
    if (taskUpdateListener === listener) taskUpdateListener = null;
    callback();
  };

  const listener = (updatedId: number, info: { status?: string }): void => {
    if (updatedId === tabId && info.status === "complete") runOnce();
  };

  taskUpdateListener = listener;
  chrome.tabs.onUpdated.addListener(listener);
  fallbackTimer = setTimeout(runOnce, TAB_READY_FALLBACK_MS);

  void chrome.tabs
    .get(tabId)
    .then((tab) => {
      if (tab.status === "complete") runOnce();
    })
    .catch(() => {});
}

function scheduleExecuteMessageRetry(task: BiliTask, startedAt: number): void {
  if (messageRetryTimeoutId !== null) clearTimeout(messageRetryTimeoutId);
  messageRetryTimeoutId = setTimeout(() => {
    messageRetryTimeoutId = null;
    sendExecuteMessage(task, startedAt);
  }, CONTENT_SCRIPT_RETRY_INTERVAL_MS);
}

function sendExecuteMessage(task: BiliTask, startedAt: number = Date.now()): void {
  if (taskTabId === null || currentTaskId !== task.id) return;
  void chrome.tabs
    .sendMessage(taskTabId, {
      action: "BILI_TASK_EXECUTE",
      data: buildBiliExecuteMessageData(task),
    })
    .catch(() => {
      if (currentTaskId !== task.id) return;
      if (Date.now() - startedAt < CONTENT_SCRIPT_READY_TIMEOUT_MS) {
        scheduleExecuteMessageRetry(task, startedAt);
        return;
      }
      void postTaskResult({
        task_id: task.id,
        status: "failed",
        error: "sendMessage_failed",
      });
      cleanupTask();
    });
}

export async function executeTask(task: BiliTask): Promise<void> {
  if (taskInFlight) return;
  if (!tryAcquireDispatcherMutex("bili")) return;

  const url = buildBiliTaskUrl(task);
  if (!url) {
    await postTaskResult({ task_id: task.id, status: "failed", error: "no_url" });
    releaseDispatcherMutex("bili");
    return;
  }

  taskInFlight = true;
  currentTaskId = task.id;

  try {
    const tab = await chrome.tabs.create({ url, active: false });
    taskTabId = tab.id ?? null;
  } catch {
    await postTaskResult({ task_id: task.id, status: "failed", error: "tab_create_failed" });
    cleanupTask();
    return;
  }

  if (taskTabId === null) {
    await postTaskResult({ task_id: task.id, status: "failed", error: "tab_id_unknown" });
    cleanupTask();
    return;
  }

  armTaskTimeout(task);
  onTabReady(taskTabId, () => sendExecuteMessage(task));
}

export async function handleBiliTaskResult(result: BiliTaskResult): Promise<void> {
  if (!taskInFlight || result.task_id !== currentTaskId) return;
  await postTaskResult(result);
  if (result.status !== "partial") {
    cleanupTask();
  }
}

async function pollNextTask(): Promise<void> {
  if (taskInFlight) return;
  const task = await fetchNextTask();
  if (!task) return;
  await executeTask(task);
}

export function startBiliTaskPolling(intervalMs: number = DEFAULT_POLL_INTERVAL_MS): void {
  if (typeof chrome === "undefined" || !chrome.alarms) return;
  chrome.alarms.create(POLL_ALARM_NAME, { periodInMinutes: intervalMs / 60_000 });
}

export function handleBiliTaskAlarm(alarmName: string): void {
  if (alarmName !== POLL_ALARM_NAME) return;
  void pollNextTask();
}

export function pollBiliTaskNow(): void {
  void pollNextTask();
}
