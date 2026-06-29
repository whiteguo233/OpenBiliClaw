/**
 * Reddit task dispatcher — background polling for extension-backed tasks.
 */

import type { RedditTaskResult, RedditTaskType } from "../content/reddit/task-executor.ts";
import { REDDIT_TASK_TAB_URL } from "../content/reddit/task-mode.ts";
import { releaseDispatcherMutex, tryAcquireDispatcherMutex } from "./dispatcher-mutex.ts";
import { apiUrl } from "../shared/backend-endpoint.ts";

const DEFAULT_POLL_INTERVAL_MS = 60_000;
const POLL_ALARM_NAME = "openbiliclaw-reddit-task-poll";
const BASE_TIMEOUT_MS = 45_000;
const PER_INPUT_MS = 25_000;
const MAX_TIMEOUT_MS = 240_000;
const CONTENT_SCRIPT_RETRY_INTERVAL_MS = 250;
const CONTENT_SCRIPT_READY_TIMEOUT_MS = 8_000;

export interface RedditTask {
  id: string;
  type: RedditTaskType;
  keywords?: string[];
  max_items_per_keyword?: number;
  source_keyword_ids?: Record<string, number>;
  subreddit?: string;
  subreddits?: string[];
  max_items?: number;
  max_items_per_subreddit?: number;
  related_urls?: string[];
  max_items_per_seed?: number;
  max_items_per_scope?: number;
  fetch_timeout_ms?: number;
}

export function isValidRedditTask(task: unknown): task is RedditTask {
  if (typeof task !== "object" || task === null) return false;
  const t = task as Record<string, unknown>;
  if (typeof t.id !== "string" || !t.id) return false;
  if (!["search", "hot", "subreddit", "related", "bootstrap_events"].includes(String(t.type))) {
    return false;
  }
  if (t.type === "bootstrap_events") {
    return true;
  }
  if (t.type === "search") {
    return Array.isArray(t.keywords) && t.keywords.length > 0;
  }
  if (t.type === "subreddit") {
    if (t.subreddits !== undefined) {
      return Array.isArray(t.subreddits) && t.subreddits.length > 0;
    }
    return typeof t.subreddit === "string" && t.subreddit.trim().length > 0;
  }
  if (t.type === "related") {
    return Array.isArray(t.related_urls) && t.related_urls.length > 0;
  }
  return true;
}

export function computeRedditTaskTimeoutMs(task: RedditTask): number {
  let breadth = 1;
  if (task.type === "search") breadth = Math.max(1, task.keywords?.length ?? 1);
  if (task.type === "subreddit") breadth = Math.max(1, task.subreddits?.length ?? 1);
  if (task.type === "related") breadth = Math.max(1, task.related_urls?.length ?? 1);
  if (task.type === "bootstrap_events") breadth = 3;
  return Math.min(Math.max(BASE_TIMEOUT_MS, BASE_TIMEOUT_MS + breadth * PER_INPUT_MS), MAX_TIMEOUT_MS);
}

export function shouldOpenRedditTaskActive(task: RedditTask): boolean {
  return task.type === "bootstrap_events";
}

let taskInFlight = false;
let taskTabId: number | null = null;
let taskTimeoutId: ReturnType<typeof setTimeout> | null = null;
let messageRetryTimeoutId: ReturnType<typeof setTimeout> | null = null;
let currentTask: RedditTask | null = null;

async function fetchNextTask(): Promise<RedditTask | null> {
  try {
    const resp = await fetch(await apiUrl("/sources/reddit/next-task"));
    if (resp.status === 204) return null;
    if (!resp.ok) return null;
    const payload: unknown = await resp.json();
    return isValidRedditTask(payload) ? payload : null;
  } catch {
    return null;
  }
}

async function postTaskResult(result: RedditTaskResult): Promise<void> {
  try {
    await fetch(await apiUrl("/sources/reddit/task-result"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch {
    // Backend transient unavailability should not crash the service worker.
  }
}

function cleanupTask(): void {
  if (taskTimeoutId !== null) {
    clearTimeout(taskTimeoutId);
    taskTimeoutId = null;
  }
  if (messageRetryTimeoutId !== null) {
    clearTimeout(messageRetryTimeoutId);
    messageRetryTimeoutId = null;
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
  taskInFlight = false;
  releaseDispatcherMutex("reddit");
}

function armTaskTimeout(task: RedditTask): void {
  taskTimeoutId = setTimeout(async () => {
    await postTaskResult({
      task_id: task.id,
      status: "failed",
      items: [],
      scope_counts: {},
      error: "task_timeout",
    });
    cleanupTask();
  }, computeRedditTaskTimeoutMs(task));
}

function onTabReady(tabId: number, callback: () => void, fallbackMs = 10_000): void {
  let completed = false;
  let fallbackTimer: ReturnType<typeof setTimeout> | null = null;
  const runOnce = (): void => {
    if (completed) return;
    completed = true;
    if (fallbackTimer !== null) clearTimeout(fallbackTimer);
    chrome.tabs.onUpdated.removeListener(listener);
    callback();
  };
  const listener = (updatedId: number, info: { status?: string }): void => {
    if (updatedId === tabId && info.status === "complete") runOnce();
  };
  chrome.tabs.onUpdated.addListener(listener);
  fallbackTimer = setTimeout(runOnce, fallbackMs);
  void chrome.tabs.get(tabId).then((tab) => {
    if (tab.status === "complete") runOnce();
  }).catch(() => {});
}

function scheduleExecuteMessageRetry(task: RedditTask, startedAt: number): void {
  if (messageRetryTimeoutId !== null) clearTimeout(messageRetryTimeoutId);
  messageRetryTimeoutId = setTimeout(() => {
    messageRetryTimeoutId = null;
    sendExecuteMessage(task, startedAt);
  }, CONTENT_SCRIPT_RETRY_INTERVAL_MS);
}

async function injectRedditContentScriptInto(tabId: number): Promise<void> {
  if (typeof chrome === "undefined" || !chrome.scripting?.executeScript) return;
  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: false },
      files: ["dist/content/reddit.js"],
      world: "ISOLATED",
    });
  } catch {
    // Manifest content_scripts normally inject reddit.js. This explicit
    // injection is a best-effort fallback for task tabs that missed it.
  }
}

function sendExecuteMessage(task: RedditTask, startedAt: number = Date.now()): void {
  if (!currentTask || currentTask.id !== task.id || taskTabId === null) return;
  const tabId = taskTabId;
  void (async () => {
    await injectRedditContentScriptInto(tabId);
    if (!currentTask || currentTask.id !== task.id) return;
    await chrome.tabs.sendMessage(tabId, {
      action: "REDDIT_TASK_EXECUTE",
      data: {
        task_id: task.id,
        type: task.type,
        keywords: task.keywords,
        max_items_per_keyword: task.max_items_per_keyword,
        source_keyword_ids: task.source_keyword_ids,
        subreddit: task.subreddit,
        subreddits: task.subreddits,
        max_items: task.max_items,
        max_items_per_subreddit: task.max_items_per_subreddit,
        related_urls: task.related_urls,
        max_items_per_seed: task.max_items_per_seed,
        max_items_per_scope: task.max_items_per_scope,
        fetch_timeout_ms: task.fetch_timeout_ms,
      },
    });
  })().catch(() => {
    if (!currentTask || currentTask.id !== task.id) return;
    if (Date.now() - startedAt < CONTENT_SCRIPT_READY_TIMEOUT_MS) {
      scheduleExecuteMessageRetry(task, startedAt);
      return;
    }
    void postTaskResult({
      task_id: task.id,
      status: "failed",
      items: [],
      scope_counts: {},
      error: "sendMessage_failed",
    });
    cleanupTask();
  });
}

export async function executeTask(task: RedditTask): Promise<void> {
  if (taskInFlight) return;
  if (!tryAcquireDispatcherMutex("reddit")) return;
  taskInFlight = true;
  currentTask = task;

  let tab: chrome.tabs.Tab;
  try {
    tab = await chrome.tabs.create({
      url: REDDIT_TASK_TAB_URL,
      active: shouldOpenRedditTaskActive(task),
    });
  } catch {
    await postTaskResult({
      task_id: task.id,
      status: "failed",
      items: [],
      scope_counts: {},
      error: "tab_create_failed",
    });
    cleanupTask();
    return;
  }

  taskTabId = tab.id ?? null;
  if (taskTabId === null) {
    await postTaskResult({
      task_id: task.id,
      status: "failed",
      items: [],
      scope_counts: {},
      error: "tab_id_unknown",
    });
    cleanupTask();
    return;
  }

  armTaskTimeout(task);
  onTabReady(taskTabId, () => sendExecuteMessage(task), 12_000);
}

export async function handleRedditTaskResult(result: RedditTaskResult): Promise<void> {
  if (!currentTask || result.task_id !== currentTask.id) return;
  await postTaskResult(result);
  cleanupTask();
}

async function pollNextTask(): Promise<void> {
  if (taskInFlight) return;
  const task = await fetchNextTask();
  if (!task) return;
  await executeTask(task);
}

export function startRedditTaskPolling(): void {
  if (typeof chrome === "undefined" || !chrome.alarms) return;
  chrome.alarms.create(POLL_ALARM_NAME, { periodInMinutes: DEFAULT_POLL_INTERVAL_MS / 60_000 });
}

export function handleRedditTaskAlarm(alarmName: string): void {
  if (alarmName === POLL_ALARM_NAME) {
    void pollNextTask();
  }
}

export function pollRedditTaskNow(): void {
  void pollNextTask();
}
