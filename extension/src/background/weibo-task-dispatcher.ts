/** Background polling for logged-in, read-only Weibo bootstrap tasks. */

import type { WeiboTaskResult, WeiboTaskType } from "../content/weibo/task-executor.ts";
import { WEIBO_TASK_TAB_URL } from "../content/weibo/task-mode.ts";
import { releaseDispatcherMutex, tryAcquireDispatcherMutex } from "./dispatcher-mutex.ts";
import { apiUrl } from "../shared/backend-endpoint.ts";
import { authenticatedFetch } from "../shared/auth.ts";
import { createTaskTab } from "./task-tab.ts";

const POLL_ALARM_NAME = "openbiliclaw-weibo-task-poll";
const DEFAULT_POLL_INTERVAL_MINUTES = 1;
const TASK_TIMEOUT_MS = 240_000;

export interface WeiboTask {
  id: string;
  type: WeiboTaskType;
  claim_token: string;
  scopes?: string[];
  max_items_per_scope?: number;
}

export function isValidWeiboTask(task: unknown): task is WeiboTask {
  if (typeof task !== "object" || task === null) return false;
  const value = task as Record<string, unknown>;
  if (typeof value.id !== "string" || !value.id) return false;
  if (value.type !== "bootstrap_events") return false;
  if (typeof value.claim_token !== "string" || !value.claim_token) return false;
  if (value.scopes !== undefined && (!Array.isArray(value.scopes) || value.scopes.some(
    (scope) => !["weibo_favorites", "weibo_following", "weibo_mentions"].includes(String(scope)),
  ))) return false;
  return true;
}

export function computeWeiboTaskTimeoutMs(_task: WeiboTask): number {
  return TASK_TIMEOUT_MS;
}

let taskInFlight = false;
let taskTabId: number | null = null;
let taskTimeoutId: ReturnType<typeof setTimeout> | null = null;
let currentTask: WeiboTask | null = null;

async function fetchNextTask(): Promise<WeiboTask | null> {
  try {
    const response = await authenticatedFetch(await apiUrl("/sources/weibo/next-task"));
    if (response.status === 204 || !response.ok) return null;
    const payload: unknown = await response.json();
    return isValidWeiboTask(payload) ? payload : null;
  } catch {
    return null;
  }
}

async function postTaskResult(result: WeiboTaskResult): Promise<void> {
  try {
    await authenticatedFetch(await apiUrl("/sources/weibo/task-result"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch {
    // A later retry/reclaim will recover the durable task row.
  }
}

function cleanupTask(): void {
  if (taskTimeoutId !== null) clearTimeout(taskTimeoutId);
  taskTimeoutId = null;
  if (taskTabId !== null) {
    try { chrome.tabs.remove(taskTabId); } catch { /* already closed */ }
  }
  taskTabId = null;
  currentTask = null;
  taskInFlight = false;
  releaseDispatcherMutex("weibo");
}

function armTaskTimeout(task: WeiboTask): void {
  taskTimeoutId = setTimeout(async () => {
    await postTaskResult({
      task_id: task.id,
      claim_token: task.claim_token,
      status: "failed",
      items: [],
      scope_counts: {},
      error: "task_timeout",
    });
    cleanupTask();
  }, computeWeiboTaskTimeoutMs(task));
}

function onTabReady(tabId: number, callback: () => void): void {
  let done = false;
  const run = (): void => {
    if (done) return;
    done = true;
    chrome.tabs.onUpdated.removeListener(listener);
    callback();
  };
  const listener = (updatedId: number, info: { status?: string }): void => {
    if (updatedId === tabId && info.status === "complete") run();
  };
  chrome.tabs.onUpdated.addListener(listener);
  setTimeout(run, 12_000);
  void chrome.tabs.get(tabId).then((tab) => { if (tab.status === "complete") run(); }).catch(() => {});
}

function sendExecuteMessage(): void {
  if (!currentTask || taskTabId === null) return;
  const task = currentTask;
  void chrome.tabs.sendMessage(taskTabId, {
    action: "WEIBO_BOOTSTRAP_EXECUTE",
    data: {
      task_id: task.id,
      claim_token: task.claim_token,
      type: task.type,
      scopes: task.scopes,
      max_items_per_scope: task.max_items_per_scope,
    },
  }).catch(() => {
    void postTaskResult({
      task_id: task.id,
      claim_token: task.claim_token,
      status: "failed",
      items: [],
      scope_counts: {},
      error: "sendMessage_failed",
    });
    cleanupTask();
  });
}

export async function executeWeiboTask(task: WeiboTask): Promise<void> {
  if (taskInFlight || !tryAcquireDispatcherMutex("weibo")) return;
  taskInFlight = true;
  currentTask = task;
  try {
    const tab = await createTaskTab({ url: WEIBO_TASK_TAB_URL, active: false });
    taskTabId = tab.id ?? null;
  } catch {
    await postTaskResult({ task_id: task.id, claim_token: task.claim_token, status: "failed", items: [], scope_counts: {}, error: "tab_create_failed" });
    cleanupTask();
    return;
  }
  if (taskTabId === null) {
    await postTaskResult({ task_id: task.id, claim_token: task.claim_token, status: "failed", items: [], scope_counts: {}, error: "tab_id_unknown" });
    cleanupTask();
    return;
  }
  armTaskTimeout(task);
  onTabReady(taskTabId, sendExecuteMessage);
}

export async function handleWeiboTaskResult(result: WeiboTaskResult): Promise<void> {
  if (!currentTask || currentTask.id !== result.task_id) return;
  await postTaskResult(result);
  cleanupTask();
}

async function pollNextTask(): Promise<void> {
  if (taskInFlight) return;
  const task = await fetchNextTask();
  if (task) await executeWeiboTask(task);
}

export function startWeiboTaskPolling(): void {
  if (typeof chrome === "undefined" || !chrome.alarms) return;
  chrome.alarms.create(POLL_ALARM_NAME, { periodInMinutes: DEFAULT_POLL_INTERVAL_MINUTES });
}

export function handleWeiboTaskAlarm(alarmName: string): void {
  if (alarmName === POLL_ALARM_NAME) void pollNextTask();
}

export function pollWeiboTaskNow(): void {
  void pollNextTask();
}
