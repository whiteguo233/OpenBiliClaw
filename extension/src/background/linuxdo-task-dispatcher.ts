/** Linux.do task dispatcher — polls the authenticated backend task queue. */

import type {
  LinuxdoCursorPosition,
  LinuxdoScope,
  LinuxdoTaskResult,
  LinuxdoTaskType,
} from "../content/linuxdo/task-executor.ts";
import {
  LINUXDO_TASK_TAB_PARAM,
  LINUXDO_TASK_TAB_URL,
} from "../content/linuxdo/task-mode.ts";
import { ASSET_PREFIX } from "../shared/asset-prefix.ts";
import { authenticatedFetch } from "../shared/auth.ts";
import { apiUrl } from "../shared/backend-endpoint.ts";
import { releaseDispatcherMutex, tryAcquireDispatcherMutex } from "./dispatcher-mutex.ts";
import { createTaskTab } from "./task-tab.ts";

const DEFAULT_POLL_INTERVAL_MS = 60_000;
const POLL_ALARM_NAME = "openbiliclaw-linuxdo-task-poll";
const TASK_SESSION_KEY = "openbiliclaw_linuxdo_task_runner";
const RUNTIME_SESSION_KEY = "openbiliclaw_linuxdo_runtime_session";
const BASE_TIMEOUT_MS = 45_000;
const DEFAULT_FETCH_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 29 * 60_000;
const CONTENT_SCRIPT_RETRY_INTERVAL_MS = 250;
const CONTENT_SCRIPT_READY_TIMEOUT_MS = 8_000;
// A task tab can race Discourse's challenge/SPA bootstrap even after the
// manifest content script has been injected.  Give the same leased task one
// bounded document restart before reporting a transport failure; never open a
// second task or release the backend lease while the first runner is still
// recoverable.
const MAX_CONTENT_SCRIPT_TAB_RESTARTS = 1;
const RECOVERY_MUTEX_RETRY_MS = 100;
const MAX_DISCOVERY_PAGES = 5;
const MAX_BOOTSTRAP_PAGES = 15;
const MAX_TASK_INPUTS = 5;
const MAX_TASK_ITEMS = 300;
const ALLOWED_SCOPES = new Set<LinuxdoScope>([
  "linuxdo_bookmarks",
  "linuxdo_likes",
  "linuxdo_read_history",
]);

export interface LinuxdoTask {
  id: string;
  claim_token: string;
  type: LinuxdoTaskType;
  scopes?: LinuxdoScope[];
  keywords?: string[];
  max_items_per_keyword?: number;
  source_keyword_ids?: Record<string, number>;
  max_items?: number;
  creator_urls?: string[];
  max_items_per_creator?: number;
  related_urls?: string[];
  max_items_per_seed?: number;
  max_items_per_scope?: number;
  max_pages?: number;
  fetch_timeout_ms?: number;
  request_interval_seconds?: number;
  cursor_contract?: "page-offset-v1";
  start_cursors?: Record<string, LinuxdoCursorPosition>;
  hydrate_topic_details?: boolean;
}

interface StoredLinuxdoTaskRunner {
  task: LinuxdoTask;
  tab_id: number;
  deadline_at: number;
  previous_active_tab_id?: number;
  final_result?: LinuxdoTaskResult;
  runtime_session_id?: string;
}

function stringArray(value: unknown): value is string[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.length <= MAX_TASK_INPUTS &&
    value.every((item) => typeof item === "string" && item.trim())
  );
}

function optionalPositiveNumber(value: unknown): boolean {
  return value === undefined || (Number.isFinite(Number(value)) && Number(value) > 0);
}

function validCursorPosition(value: unknown): boolean {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return (
    Number.isInteger(Number(row.page)) &&
    Number(row.page) >= 0 &&
    Number(row.page) <= 100_000 &&
    Number.isInteger(Number(row.offset)) &&
    Number(row.offset) >= 0 &&
    Number(row.offset) <= 10_000
  );
}

function isLinuxdoRunnerUrl(value: unknown): boolean {
  try {
    const parsed = new URL(String(value ?? ""));
    return parsed.protocol === "https:" && parsed.hostname === "linux.do";
  } catch {
    return false;
  }
}

export function isValidLinuxdoTask(task: unknown): task is LinuxdoTask {
  if (typeof task !== "object" || task === null) return false;
  const row = task as Record<string, unknown>;
  if (typeof row.id !== "string" || !row.id.trim()) return false;
  if (typeof row.claim_token !== "string" || !row.claim_token.trim()) return false;
  if (![
    "bootstrap_events",
    "search",
    "hot",
    "feed",
    "creator",
    "related",
  ].includes(String(row.type))) return false;
  if (row.type === "search" && !stringArray(row.keywords)) return false;
  if (row.type === "creator" && !stringArray(row.creator_urls)) return false;
  if (row.type === "related" && !stringArray(row.related_urls)) return false;
  if (row.scopes !== undefined) {
    if (
      !Array.isArray(row.scopes) ||
      row.scopes.length === 0 ||
      row.scopes.some((scope) => !ALLOWED_SCOPES.has(String(scope) as LinuxdoScope))
    ) {
      return false;
    }
  }
  for (const field of [
    "max_items_per_keyword",
    "max_items",
    "max_items_per_creator",
    "max_items_per_seed",
    "max_items_per_scope",
    "max_pages",
    "fetch_timeout_ms",
  ]) {
    if (!optionalPositiveNumber(row[field])) return false;
  }
  const maxPages = row.type === "bootstrap_events"
    ? MAX_BOOTSTRAP_PAGES
    : MAX_DISCOVERY_PAGES;
  if (row.max_pages !== undefined && Number(row.max_pages) > maxPages) return false;
  for (const field of [
    "max_items_per_keyword",
    "max_items",
    "max_items_per_creator",
    "max_items_per_seed",
    "max_items_per_scope",
  ]) {
    if (row[field] !== undefined && Number(row[field]) > MAX_TASK_ITEMS) return false;
  }
  if (
    row.fetch_timeout_ms !== undefined &&
    Number(row.fetch_timeout_ms) > DEFAULT_FETCH_TIMEOUT_MS
  ) return false;
  if (
    row.request_interval_seconds !== undefined &&
    (!Number.isFinite(Number(row.request_interval_seconds)) ||
      Number(row.request_interval_seconds) < 0 ||
      Number(row.request_interval_seconds) > 30)
  ) return false;
  if (row.cursor_contract !== undefined && row.cursor_contract !== "page-offset-v1") return false;
  if (row.cursor_contract === "page-offset-v1") {
    if (!["search", "hot", "feed", "creator"].includes(String(row.type))) return false;
    if (
      typeof row.start_cursors !== "object" ||
      row.start_cursors === null ||
      Array.isArray(row.start_cursors)
    ) return false;
    const cursors = Object.entries(row.start_cursors as Record<string, unknown>);
    const expectedKeys = row.type === "search"
      ? (row.keywords as string[])
      : row.type === "creator"
      ? (row.creator_urls as string[])
      : ["default"];
    const actualKeys = cursors.map(([key]) => key).sort();
    if (
      cursors.length === 0 ||
      cursors.length > MAX_TASK_INPUTS ||
      JSON.stringify(actualKeys) !== JSON.stringify([...new Set(expectedKeys)].sort())
    ) return false;
    if (cursors.some(([key, value]) => !key.trim() || !validCursorPosition(value))) return false;
  } else if (row.start_cursors !== undefined) {
    return false;
  }
  if (
    row.hydrate_topic_details !== undefined &&
    (row.hydrate_topic_details !== true || !["search", "related"].includes(String(row.type)))
  ) return false;
  return true;
}

function isValidLinuxdoTaskResult(
  result: unknown,
  task: LinuxdoTask | undefined,
): result is LinuxdoTaskResult {
  if (!task || typeof result !== "object" || result === null) return false;
  const row = result as Record<string, unknown>;
  return (
    row.task_id === task.id &&
    row.claim_token === task.claim_token &&
    ["ok", "empty", "degraded", "failed"].includes(String(row.status)) &&
    Array.isArray(row.items) &&
    typeof row.scope_counts === "object" &&
    row.scope_counts !== null &&
    !Array.isArray(row.scope_counts)
  );
}

function isValidLinuxdoTaskResultEnvelope(result: unknown): result is LinuxdoTaskResult {
  if (typeof result !== "object" || result === null) return false;
  const row = result as Record<string, unknown>;
  return (
    typeof row.task_id === "string" &&
    Boolean(row.task_id.trim()) &&
    typeof row.claim_token === "string" &&
    Boolean(row.claim_token.trim()) &&
    ["ok", "empty", "degraded", "failed"].includes(String(row.status)) &&
    Array.isArray(row.items) &&
    typeof row.scope_counts === "object" &&
    row.scope_counts !== null &&
    !Array.isArray(row.scope_counts)
  );
}

export function computeLinuxdoTaskTimeoutMs(task: LinuxdoTask): number {
  let breadth = 1;
  if (task.type === "bootstrap_events") breadth = Math.max(1, task.scopes?.length ?? 3);
  if (task.type === "search") breadth = Math.max(1, task.keywords?.length ?? 1);
  if (task.type === "creator") breadth = Math.max(1, task.creator_urls?.length ?? 1);
  if (task.type === "related") breadth = Math.max(1, task.related_urls?.length ?? 1);
  const defaultPages = task.type === "bootstrap_events"
    ? Math.max(5, Math.ceil(Number(task.max_items_per_scope ?? task.max_items ?? 300) / 20))
    : 5;
  const pages = Math.min(
    Math.max(1, Math.floor(Number(task.max_pages) || defaultPages)),
    task.type === "bootstrap_events" ? MAX_BOOTSTRAP_PAGES : MAX_DISCOVERY_PAGES,
  );
  const fetchTimeoutMs = Math.min(
    DEFAULT_FETCH_TIMEOUT_MS,
    Math.max(1, Math.floor(Number(task.fetch_timeout_ms) || DEFAULT_FETCH_TIMEOUT_MS)),
  );
  const intervalMs = Math.min(
    30_000,
    Math.max(0, Math.ceil(Number(task.request_interval_seconds) * 1000) || 0),
  );
  const perRequestMs = Math.max(1_000, fetchTimeoutMs, intervalMs);
  let requestCount = breadth * (pages + 1);
  if (task.type === "bootstrap_events") requestCount += 1;
  if (task.type === "related") requestCount = breadth * 2;
  if (task.type === "hot") requestCount = (pages + 1) * 2;
  if (task.hydrate_topic_details) {
    requestCount += Math.min(MAX_TASK_ITEMS, Math.max(1, Number(task.max_items) || 20));
  }
  return Math.min(
    Math.max(BASE_TIMEOUT_MS, BASE_TIMEOUT_MS + requestCount * perRequestMs),
    MAX_TIMEOUT_MS,
  );
}

export function shouldOpenLinuxdoTaskActive(task: LinuxdoTask): boolean {
  return task.type === "bootstrap_events";
}

let taskInFlight = false;
let taskTabId: number | null = null;
let taskTimeoutId: ReturnType<typeof setTimeout> | null = null;
let messageRetryTimeoutId: ReturnType<typeof setTimeout> | null = null;
let resultRetryTimeoutId: ReturnType<typeof setTimeout> | null = null;
let currentTask: LinuxdoTask | null = null;
let previousActiveTabId: number | null = null;
let pendingFinalResult: LinuxdoTaskResult | null = null;
let recoveryPromise: Promise<void> | null = null;
let runnerStorageMutation: Promise<void> = Promise.resolve();
let runtimeSessionPromise: Promise<string> | null = null;
let contentScriptTabRestarts = 0;

function newRuntimeSessionId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

function runtimeSessionId(): Promise<string> {
  runtimeSessionPromise ??= (async () => {
    const storage = typeof chrome === "undefined" ? null : chrome.storage?.session ?? null;
    if (!storage) return newRuntimeSessionId();
    try {
      const existing = (await storage.get(RUNTIME_SESSION_KEY))[RUNTIME_SESSION_KEY];
      if (typeof existing === "string" && existing.trim()) return existing;
      const created = newRuntimeSessionId();
      await storage.set({ [RUNTIME_SESSION_KEY]: created });
      return created;
    } catch {
      return newRuntimeSessionId();
    }
  })();
  return runtimeSessionPromise;
}

function runnerStorageArea(): chrome.storage.StorageArea | null {
  try {
    // `storage.session` survives an MV3 worker recycle but is cleared by
    // `chrome.runtime.reload()`. The bounded, credential-free runner record
    // must also survive a development hot reload so the same leased task can
    // resume and terminally complete.
    return typeof chrome === "undefined"
      ? null
      : chrome.storage?.local ?? chrome.storage?.session ?? null;
  } catch {
    return null;
  }
}

function mutateRunnerStorage(mutation: () => Promise<void>): Promise<void> {
  const current = runnerStorageMutation.then(mutation, mutation);
  runnerStorageMutation = current.catch(() => {});
  return current;
}

async function persistTaskRunner(
  task: LinuxdoTask,
  tabId: number,
  deadlineAt: number,
  finalResult: LinuxdoTaskResult | null = pendingFinalResult,
): Promise<void> {
  const runtimeSession = await runtimeSessionId();
  await mutateRunnerStorage(async () => {
    const storage = runnerStorageArea();
    if (!storage) return;
    await storage.set({
      [TASK_SESSION_KEY]: {
        task,
        tab_id: tabId,
        deadline_at: deadlineAt,
        ...(previousActiveTabId !== null
          ? { previous_active_tab_id: previousActiveTabId }
          : {}),
        ...(finalResult ? { final_result: finalResult } : {}),
        runtime_session_id: runtimeSession,
      },
    });
  }).catch(() => {});
}

function clearPersistedTaskRunner(): Promise<void> {
  return mutateRunnerStorage(async () => {
    const storage = runnerStorageArea();
    if (storage) await storage.remove(TASK_SESSION_KEY);
  }).catch(() => {});
}

async function fetchNextTask(): Promise<LinuxdoTask | null> {
  try {
    const response = await authenticatedFetch(await apiUrl("/sources/linuxdo/next-task"));
    if (response.status === 204 || !response.ok) return null;
    const payload: unknown = await response.json();
    if (isValidLinuxdoTask(payload)) return payload;
    const taskId = typeof payload === "object" && payload !== null
      ? String((payload as Record<string, unknown>).id ?? "").trim()
      : "";
    const claimToken = typeof payload === "object" && payload !== null
      ? String((payload as Record<string, unknown>).claim_token ?? "").trim()
      : "";
    if (taskId && claimToken) {
      // The row is already leased even though its execution shape is invalid.
      // Give the rejection the same durable first-final/ACK semantics as every
      // normal task failure: otherwise a transient backend outage here would
      // release the local mutex and strand the claim until the 35-minute lease
      // expires. The minimal owner stores only id/token plus a harmless valid
      // type; the rejected upstream payload itself never enters extension
      // storage.
      const rejectionOwner: LinuxdoTask = {
        id: taskId,
        claim_token: claimToken,
        type: "feed",
      };
      const rejection: LinuxdoTaskResult = {
        task_id: taskId,
        claim_token: claimToken,
        status: "failed",
        items: [],
        scope_counts: {},
        error: "invalid_task_payload",
      };
      taskInFlight = true;
      currentTask = rejectionOwner;
      taskTabId = null;
      pendingFinalResult = rejection;
      const deadlineAt = Date.now() + computeLinuxdoTaskTimeoutMs(rejectionOwner);
      await persistTaskRunner(rejectionOwner, -1, deadlineAt, rejection);
      await deliverTaskFinal(rejection);
    }
    return null;
  } catch {
    return null;
  }
}

type TaskResultDelivery = "acked" | "rejected" | "retry";

async function postTaskResult(result: LinuxdoTaskResult): Promise<TaskResultDelivery> {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await authenticatedFetch(await apiUrl("/sources/linuxdo/task-result"), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(result),
      });
      if (response.ok) return "acked";
      // Claim loss and schema rejection are deterministic. Retrying the same
      // payload forever would hold the global task mutex after the backend has
      // already assigned a new owner (or refused this result contract).
      if (response.status === 409 || response.status === 422) return "rejected";
    } catch {
      // Retry the exact immutable final. The backend's first-final marker
      // makes an ACK loss safe to replay without changing the canonical row.
    }
    if (attempt < 2) {
      await new Promise<void>((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
    }
  }
  return "retry";
}

function cleanupTask(): void {
  if (taskTimeoutId !== null) clearTimeout(taskTimeoutId);
  if (messageRetryTimeoutId !== null) clearTimeout(messageRetryTimeoutId);
  if (resultRetryTimeoutId !== null) clearTimeout(resultRetryTimeoutId);
  taskTimeoutId = null;
  messageRetryTimeoutId = null;
  resultRetryTimeoutId = null;
  if (previousActiveTabId !== null && previousActiveTabId !== taskTabId) {
    void chrome.tabs.update(previousActiveTabId, { active: true }).catch(() => {});
  }
  if (taskTabId !== null) {
    try {
      chrome.tabs.remove(taskTabId);
    } catch {
      // The user or MV3 recovery may already have closed the runner-owned tab.
    }
  }
  taskTabId = null;
  currentTask = null;
  previousActiveTabId = null;
  pendingFinalResult = null;
  taskInFlight = false;
  contentScriptTabRestarts = 0;
  void clearPersistedTaskRunner();
  releaseDispatcherMutex("linuxdo");
}

async function deliverTaskFinal(result: LinuxdoTaskResult): Promise<boolean> {
  pendingFinalResult = result;
  if (currentTask) {
    const deadlineAt = Date.now() + computeLinuxdoTaskTimeoutMs(currentTask);
    await persistTaskRunner(currentTask, taskTabId ?? -1, deadlineAt, result);
  }
  const delivery = await postTaskResult(result);
  if (delivery === "acked" || delivery === "rejected") {
    cleanupTask();
    return true;
  }
  if (resultRetryTimeoutId !== null) clearTimeout(resultRetryTimeoutId);
  resultRetryTimeoutId = setTimeout(() => {
    resultRetryTimeoutId = null;
    if (pendingFinalResult?.task_id === result.task_id) {
      void deliverTaskFinal(result);
    }
  }, 2_000);
  return false;
}

function armTaskTimeout(
  task: LinuxdoTask,
  deadlineAt = Date.now() + computeLinuxdoTaskTimeoutMs(task),
): void {
  taskTimeoutId = setTimeout(async () => {
    await deliverTaskFinal({
      task_id: task.id,
      claim_token: task.claim_token,
      status: "failed",
      items: [],
      scope_counts: {},
      error: "task_timeout",
    });
  }, Math.max(0, deadlineAt - Date.now()));
}

function onTabReady(tabId: number, callback: () => void, fallbackMs = 12_000): void {
  let complete = false;
  let fallbackTimer: ReturnType<typeof setTimeout> | null = null;
  const runOnce = (): void => {
    if (complete) return;
    complete = true;
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

async function injectLinuxdoContentScript(tabId: number): Promise<void> {
  if (typeof chrome === "undefined" || !chrome.scripting?.executeScript) return;
  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: false },
      files: [`${ASSET_PREFIX}content/linuxdo.js`],
      world: "ISOLATED",
    });
  } catch {
    // Manifest injection is primary; this only recovers a task tab that raced it.
  }
}

function scheduleExecuteMessageRetry(task: LinuxdoTask, startedAt: number): void {
  if (messageRetryTimeoutId !== null) clearTimeout(messageRetryTimeoutId);
  messageRetryTimeoutId = setTimeout(() => {
    messageRetryTimeoutId = null;
    sendExecuteMessage(task, startedAt);
  }, CONTENT_SCRIPT_RETRY_INTERVAL_MS);
}

async function restartLinuxdoTaskTab(task: LinuxdoTask): Promise<boolean> {
  if (
    contentScriptTabRestarts >= MAX_CONTENT_SCRIPT_TAB_RESTARTS ||
    !currentTask ||
    currentTask.id !== task.id ||
    taskTabId === null
  ) {
    return false;
  }
  contentScriptTabRestarts += 1;
  const tabId = taskTabId;
  if (messageRetryTimeoutId !== null) clearTimeout(messageRetryTimeoutId);
  messageRetryTimeoutId = null;
  try {
    await chrome.tabs.reload(tabId);
  } catch {
    try {
      await chrome.tabs.update(tabId, { url: LINUXDO_TASK_TAB_URL });
    } catch {
      return false;
    }
  }
  if (!currentTask || currentTask.id !== task.id || taskTabId !== tabId) return false;
  onTabReady(tabId, () => sendExecuteMessage(task), 12_000);
  return true;
}

function sendExecuteMessage(task: LinuxdoTask, startedAt = Date.now()): void {
  if (!currentTask || currentTask.id !== task.id || taskTabId === null) return;
  const tabId = taskTabId;
  void (async () => {
    await injectLinuxdoContentScript(tabId);
    if (!currentTask || currentTask.id !== task.id) return;
    await chrome.tabs.sendMessage(tabId, {
      action: "LINUXDO_TASK_EXECUTE",
      data: {
        task_id: task.id,
        claim_token: task.claim_token,
        type: task.type,
        scopes: task.scopes,
        keywords: task.keywords,
        max_items_per_keyword: task.max_items_per_keyword,
        source_keyword_ids: task.source_keyword_ids,
        max_items: task.max_items,
        creator_urls: task.creator_urls,
        max_items_per_creator: task.max_items_per_creator,
        related_urls: task.related_urls,
        max_items_per_seed: task.max_items_per_seed,
        max_items_per_scope: task.max_items_per_scope,
        max_pages: task.max_pages,
        fetch_timeout_ms: task.fetch_timeout_ms,
        request_interval_seconds: task.request_interval_seconds,
        cursor_contract: task.cursor_contract,
        start_cursors: task.start_cursors,
        hydrate_topic_details: task.hydrate_topic_details,
      },
    });
  })().catch(() => {
    if (!currentTask || currentTask.id !== task.id) return;
    if (Date.now() - startedAt < CONTENT_SCRIPT_READY_TIMEOUT_MS) {
      scheduleExecuteMessageRetry(task, startedAt);
      return;
    }
    void (async () => {
      if (await restartLinuxdoTaskTab(task)) return;
      await deliverTaskFinal({
        task_id: task.id,
        claim_token: task.claim_token,
        status: "failed",
        items: [],
        scope_counts: {},
        error: "sendMessage_failed",
      });
    })();
  });
}

async function beginLinuxdoTask(task: LinuxdoTask, mutexAlreadyHeld: boolean): Promise<void> {
  if (taskInFlight) {
    if (mutexAlreadyHeld) releaseDispatcherMutex("linuxdo");
    return;
  }
  if (!mutexAlreadyHeld && !tryAcquireDispatcherMutex("linuxdo")) return;
  taskInFlight = true;
  currentTask = task;
  contentScriptTabRestarts = 0;
  pendingFinalResult = null;
  previousActiveTabId = null;
  if (shouldOpenLinuxdoTaskActive(task)) {
    try {
      const activeTabs = await chrome.tabs.query({ active: true, currentWindow: true });
      const activeId = activeTabs.find((candidate) => typeof candidate.id === "number")?.id;
      previousActiveTabId = typeof activeId === "number" ? activeId : null;
    } catch {
      previousActiveTabId = null;
    }
  }
  let tab: chrome.tabs.Tab;
  try {
    tab = await createTaskTab({
      url: LINUXDO_TASK_TAB_URL,
      active: shouldOpenLinuxdoTaskActive(task),
    });
  } catch {
    await deliverTaskFinal({
      task_id: task.id,
      claim_token: task.claim_token,
      status: "failed",
      items: [],
      scope_counts: {},
      error: "tab_create_failed",
    });
    return;
  }
  taskTabId = tab.id ?? null;
  if (taskTabId === null) {
    await deliverTaskFinal({
      task_id: task.id,
      claim_token: task.claim_token,
      status: "failed",
      items: [],
      scope_counts: {},
      error: "tab_id_unknown",
    });
    return;
  }
  const deadlineAt = Date.now() + computeLinuxdoTaskTimeoutMs(task);
  await persistTaskRunner(task, taskTabId, deadlineAt);
  armTaskTimeout(task, deadlineAt);
  onTabReady(taskTabId, () => sendExecuteMessage(task));
}

export async function executeLinuxdoTask(task: LinuxdoTask): Promise<void> {
  await ensureLinuxdoTaskRecovery();
  await beginLinuxdoTask(task, false);
}

export async function handleLinuxdoTaskResult(
  result: LinuxdoTaskResult,
  senderTab?: { id?: number; url?: string },
): Promise<void> {
  await ensureLinuxdoTaskRecovery();
  if (!isValidLinuxdoTaskResultEnvelope(result)) {
    throw new Error("linuxdo_task_result_invalid");
  }
  if (currentTask) {
    if (!isValidLinuxdoTaskResult(result, currentTask)) {
      throw new Error("linuxdo_task_result_owner_mismatch");
    }
    if (
      typeof senderTab?.id === "number" &&
      taskTabId !== null &&
      senderTab.id !== taskTabId
    ) {
      throw new Error("linuxdo_task_result_tab_mismatch");
    }
    if (!(await deliverTaskFinal(result))) {
      throw new Error("linuxdo_task_result_post_failed");
    }
    return;
  }

  // Narrow recovery fallback for a result that woke a worker after local
  // runner metadata was lost. The marker proves it came from a runner-owned
  // Linux.do tab; post it directly so it cannot clean up or overwrite a newer
  // in-memory task.
  if (!String(senderTab?.url ?? "").includes(`${LINUXDO_TASK_TAB_PARAM}=1`)) {
    throw new Error("linuxdo_task_result_runner_missing");
  }
  const delivery = await postTaskResult(result);
  if (delivery === "retry") throw new Error("linuxdo_task_result_post_failed");
  if (typeof senderTab?.id === "number") {
    await chrome.tabs.remove(senderTab.id).catch(() => {});
  }
}

async function pollNextTask(): Promise<void> {
  await ensureLinuxdoTaskRecovery();
  if (taskInFlight) return;
  // Acquire before claiming from the backend. Otherwise another source can
  // own the shared task tab while this queue row is already leased, leaving it
  // invisible until stale-lease recovery.
  if (!tryAcquireDispatcherMutex("linuxdo")) return;
  const task = await fetchNextTask();
  if (!task) {
    // An invalid claimed payload may have installed a durable rejection
    // outbox. Keep the mutex while its exact final awaits backend ACK.
    if (!taskInFlight) releaseDispatcherMutex("linuxdo");
    return;
  }
  await beginLinuxdoTask(task, true);
}

async function recoverLinuxdoTaskRunner(): Promise<void> {
  // Acquire before the first await so cold-start recovery closes the shared
  // claim gate synchronously. If another source briefly owns the slot, keep
  // this recovery promise pending and retry instead of caching a false success.
  let ownsMutex = tryAcquireDispatcherMutex("linuxdo");
  const storage = runnerStorageArea();
  if (!storage) {
    if (ownsMutex) releaseDispatcherMutex("linuxdo");
    return;
  }
  if (!ownsMutex) {
    // Do not delay an unrelated source when there is provably no Linux.do
    // runner to restore. A stored row (even a malformed one) must wait for the
    // slot so it can be authoritatively restored or cleared under the mutex.
    try {
      const preliminary = (await storage.get(TASK_SESSION_KEY))[TASK_SESSION_KEY];
      if (typeof preliminary !== "object" || preliminary === null) return;
    } catch {
      // A transient storage read cannot prove that recovery has no work.
    }
  }
  while (!ownsMutex) {
    await new Promise((resolve) => setTimeout(resolve, RECOVERY_MUTEX_RETRY_MS));
    ownsMutex = tryAcquireDispatcherMutex("linuxdo");
  }

  let restoredRunner = false;
  try {
    let stored: unknown;
    try {
      stored = (await storage.get(TASK_SESSION_KEY))[TASK_SESSION_KEY];
    } catch {
      return;
    }
    if (typeof stored !== "object" || stored === null) {
      await clearPersistedTaskRunner();
      return;
    }
    const row = stored as Partial<StoredLinuxdoTaskRunner>;
    if (
      !isValidLinuxdoTask(row.task) ||
      !Number.isInteger(row.tab_id) ||
      (Number(row.tab_id) < 0 && !isValidLinuxdoTaskResult(row.final_result, row.task)) ||
      !Number.isFinite(row.deadline_at)
    ) {
      await clearPersistedTaskRunner();
      return;
    }
    const tabId = Number(row.tab_id);
    previousActiveTabId = Number.isInteger(row.previous_active_tab_id)
      ? Number(row.previous_active_tab_id)
      : null;
    if (isValidLinuxdoTaskResult(row.final_result, row.task)) {
      taskInFlight = true;
      currentTask = row.task;
      taskTabId = tabId >= 0 ? tabId : null;
      pendingFinalResult = row.final_result;
      restoredRunner = true;
      await deliverTaskFinal(row.final_result);
      return;
    }
    let tab: chrome.tabs.Tab;
    try {
      tab = await chrome.tabs.get(tabId);
    } catch {
      await clearPersistedTaskRunner();
      return;
    }
    // Full-page auth redirects may legitimately drop the query marker. The
    // durable runner row already binds this exact tab id to the leased task;
    // keep the origin fence, but do not mistake a redirect for ownership loss.
    if (!isLinuxdoRunnerUrl(tab.url)) {
      await clearPersistedTaskRunner();
      return;
    }
    if (Number(row.deadline_at) <= Date.now()) {
      taskInFlight = true;
      currentTask = row.task;
      taskTabId = tabId;
      restoredRunner = true;
      await deliverTaskFinal({
        task_id: row.task.id,
        claim_token: row.task.claim_token,
        status: "failed",
        items: [],
        scope_counts: {},
        error: "task_timeout",
      });
      return;
    }
    taskInFlight = true;
    currentTask = row.task;
    taskTabId = tabId;
    armTaskTimeout(row.task, Number(row.deadline_at));
    restoredRunner = true;
    const currentRuntimeSession = await runtimeSessionId();
    const requiresFreshDocument = row.runtime_session_id !== currentRuntimeSession;
    await persistTaskRunner(row.task, tabId, Number(row.deadline_at));
    if (requiresFreshDocument) {
      // A full extension reload invalidates the old content-script context while
      // leaving the runner tab and local recovery row alive. Force a same-origin
      // document reload so the new extension generation owns a fresh listener.
      try {
        await chrome.tabs.reload(tabId);
      } catch {
        await chrome.tabs.update(tabId, { url: LINUXDO_TASK_TAB_URL });
      }
      onTabReady(tabId, () => sendExecuteMessage(row.task!));
    } else {
      // An ordinary MV3 worker recycle preserves storage.session and the page
      // context; replaying the same task is coalesced by the content executor.
      sendExecuteMessage(row.task);
    }
  } finally {
    if (!restoredRunner) releaseDispatcherMutex("linuxdo");
  }
}

/** Restore the runner-owned task before any alarm or kick can claim new work. */
export function ensureLinuxdoTaskRecovery(): Promise<void> {
  recoveryPromise ??= recoverLinuxdoTaskRunner();
  return recoveryPromise;
}

/** Test-only MV3 recycle seam: retain session metadata, discard memory state. */
export function resetLinuxdoTaskRuntimeForTest(): void {
  if (taskTimeoutId !== null) clearTimeout(taskTimeoutId);
  if (messageRetryTimeoutId !== null) clearTimeout(messageRetryTimeoutId);
  taskTimeoutId = null;
  messageRetryTimeoutId = null;
  if (resultRetryTimeoutId !== null) clearTimeout(resultRetryTimeoutId);
  resultRetryTimeoutId = null;
  taskInFlight = false;
  contentScriptTabRestarts = 0;
  taskTabId = null;
  currentTask = null;
  previousActiveTabId = null;
  pendingFinalResult = null;
  recoveryPromise = null;
  runnerStorageMutation = Promise.resolve();
  runtimeSessionPromise = null;
  releaseDispatcherMutex("linuxdo");
}

export function startLinuxdoTaskPolling(): void {
  if (typeof chrome === "undefined" || !chrome.alarms) return;
  chrome.alarms.create(POLL_ALARM_NAME, {
    periodInMinutes: DEFAULT_POLL_INTERVAL_MS / 60_000,
  });
}

export function handleLinuxdoTaskAlarm(alarmName: string): Promise<void> {
  return alarmName === POLL_ALARM_NAME ? pollNextTask() : Promise.resolve();
}

export function pollLinuxdoTaskNow(): Promise<void> {
  return pollNextTask();
}
