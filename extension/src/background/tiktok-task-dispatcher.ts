/**
 * TikTok task dispatcher — background polling for account-behavior
 * bootstrap tasks (tt_like / tt_collect / tt_history / tt_follow).
 *
 * Mirrors dy-task-dispatcher.ts structurally but is deliberately
 * one backend bootstrap_profile task carries a frozen scopes[] list
 * and max_items_per_scope; its scopes execute sequentially in one owned tab, and collection is DOM-only — no MAIN-world
 * fetch-tap, no request signing (contract
 * docs/platform-source-contract.tiktok.toml: extension.task =
 * "browser-task", background = true).
 *
 * Flow: poll → claim → open a background tiktok.com tab (task marker
 * query param) → TIKTOK_SCOPE_EXECUTE to the content script →
 * TIKTOK_SCOPE_RESULT back → two-stage staged completion: a partial
 * POST carrying the normalized rows, then the terminal POST. Both POSTs
 * go through the pending-result slot in chrome.storage.local: the exact
 * canonical body is persisted BEFORE the POST and retried with bounded
 * backoff until a 2xx ACK or an explicit 4xx terminal rejection
 * (contract [task] retry clause). A fresh worker flushes the pending
 * slot before claiming new work.
 *
 * MV3 resilience mirrors dy: the in-flight task snapshot (task, tab,
 * deadline, progress) lives in chrome.storage.session and the deadline
 * is backed by a chrome.alarms one-shot; ensureTiktokTaskRecovery()
 * restores the snapshot on wake, so late results still post, the
 * deadline backstop still closes the task tab, and orphaned marker
 * tabs are swept.
 */

import {
  TIKTOK_SCOPES,
  type TiktokBootstrapItem,
  type TiktokScope,
  type TiktokScopeResult,
} from "../content/tt/task-executor.ts";
import { apiUrl } from "../shared/backend-endpoint.ts";
import { authenticatedFetch } from "../shared/auth.ts";
import {
  isNativeSaveTask,
  type NativeSaveResult,
  type NativeSaveTask,
} from "../shared/native-save.ts";
import { withTaskTabMarker } from "../shared/task-tab.ts";
import {
  releaseDispatcherMutex,
  tryAcquireDispatcherMutex,
} from "./dispatcher-mutex.ts";
import {
  ensureNativeSaveTaskRecovery,
  runNativeSaveTask,
} from "./native-save-task-runner.ts";
import { createTaskTab } from "./task-tab.ts";

const TIKTOK_TASK_MARKER = "openbiliclaw_tiktok_task";
const MUTEX_LABEL = "tiktok";

const DEFAULT_POLL_INTERVAL_MS = 60_000;
// Include login hydration plus an owned-profile navigation/resume before
// spending the separately budgeted scroll rounds.
const BASE_TIMEOUT_MS = 90_000;
const PER_ROUND_TIMEOUT_MS = 3_000;
const MAX_TASK_TIMEOUT_MS = 360_000;
const TASK_IDLE_TIMEOUT_MS = 120_000;
const TIKTOK_CONTENT_SCOPE_KEY = "openbiliclaw_tiktok_content_scope_v1";
const POLL_ALARM_NAME = "openbiliclaw-tiktok-task-poll";
const TASK_TIMEOUT_ALARM_NAME = "openbiliclaw-tiktok-task-timeout";
export const TIKTOK_TASK_STATE_SESSION_KEY = "openbiliclaw_tiktok_active_task";
export const TIKTOK_PENDING_RESULT_LOCAL_KEY = "openbiliclaw_tiktok_pending_task_result";

const DEFAULT_MAX_ITEMS = 300;
const DEFAULT_SCROLL_ROUNDS = 15;
const DEFAULT_STAGNANT_ROUNDS = 5;

// ---------------------------------------------------------------------------
// Task types
// ---------------------------------------------------------------------------

export interface TiktokScopeTask {
  id: string;
  /** The task type IS the bootstrap scope. */
  type: TiktokScope;
  /** The backend spreads payload_json at the top level of next-task. */
  max_items?: number;
  max_scroll_rounds?: number;
  max_stagnant_scroll_rounds?: number;
  /** Tolerated legacy nesting (defensive; not what the backend emits). */
  payload?: {
    max_items?: number;
    max_scroll_rounds?: number;
    max_stagnant_scroll_rounds?: number;
  };
}

/** Exact next-task shape emitted by enqueue_tiktok_bootstrap. */
export interface TiktokBootstrapTask {
  id: string;
  type: "bootstrap_profile";
  scopes: TiktokScope[];
  max_items_per_scope: number;
  max_scroll_rounds: number;
  max_stagnant_scroll_rounds?: number;
}

type TiktokAccountTask = TiktokScopeTask | TiktokBootstrapTask;
export type TiktokTask = TiktokAccountTask | NativeSaveTask;

export interface TiktokTaskResult {
  task_id: string;
  status: "ok" | "empty" | "partial" | "degraded" | "failed";
  videos?: TiktokBootstrapItem[];
  scope_counts?: Record<string, number>;
  error?: string;
  debug?: Record<string, unknown>;
  viewer_handle?: string;
  viewer_sec_uid?: string;
}

export type TiktokTaskCallbackResult = TiktokTaskResult | NativeSaveResult;

export type TiktokTaskExecutionDisposition = "accepted" | "declined";

// ---------------------------------------------------------------------------
// Pure helpers (testable without chrome)
// ---------------------------------------------------------------------------

function isValidLimitField(value: unknown): boolean {
  if (value === undefined) return true;
  return typeof value === "number" && Number.isFinite(value);
}

export function isValidTiktokTask(task: unknown): task is TiktokTask {
  if (isNativeSaveTask(task)) {
    return task.platform === "tiktok" && task.platform_slug === "tiktok";
  }
  if (typeof task !== "object" || task === null) return false;
  const t = task as Record<string, unknown>;
  if (typeof t.id !== "string" || !t.id) return false;
  if (t.type === "bootstrap_profile") {
    return Array.isArray(t.scopes) && t.scopes.length > 0 &&
      t.scopes.length <= TIKTOK_SCOPES.length && new Set(t.scopes).size === t.scopes.length &&
      t.scopes.every((scope) => TIKTOK_SCOPES.includes(scope as TiktokScope)) &&
      typeof t.max_items_per_scope === "number" && Number.isInteger(t.max_items_per_scope) &&
      t.max_items_per_scope > 0 && typeof t.max_scroll_rounds === "number" &&
      Number.isInteger(t.max_scroll_rounds) && t.max_scroll_rounds >= 0 &&
      isValidLimitField(t.max_stagnant_scroll_rounds);
  }
  if (!TIKTOK_SCOPES.includes(t.type as TiktokScope)) return false;
  for (const key of ["max_items", "max_scroll_rounds", "max_stagnant_scroll_rounds"]) {
    if (!isValidLimitField(t[key])) return false;
  }
  if (t.payload !== undefined) {
    if (typeof t.payload !== "object" || t.payload === null) return false;
    const p = t.payload as Record<string, unknown>;
    for (const key of ["max_items", "max_scroll_rounds", "max_stagnant_scroll_rounds"]) {
      if (!isValidLimitField(p[key])) return false;
    }
  }
  return true;
}

function taskLimit(task: TiktokScopeTask, key: "max_items" | "max_scroll_rounds" | "max_stagnant_scroll_rounds"): number | undefined {
  const topLevel = task[key];
  if (typeof topLevel === "number" && Number.isFinite(topLevel)) return topLevel;
  const nested = task.payload?.[key];
  return typeof nested === "number" && Number.isFinite(nested) ? nested : undefined;
}

export function buildTiktokTaskUrl(task: TiktokTask): string {
  if (task.type === "native_save") return task.content_url;
  // Every scope starts from the tiktok.com homepage; the content script
  // drives click-based navigation from there (direct deep links trip
  // risk control on the Douyin sibling; same discipline here).
  return withTaskTabMarker("https://www.tiktok.com/", TIKTOK_TASK_MARKER);
}

function taskScopes(task: TiktokAccountTask): TiktokScope[] {
  return task.type === "bootstrap_profile" ? task.scopes : [task.type];
}

function scopeTask(task: TiktokAccountTask, scope: TiktokScope): TiktokScopeTask {
  return task.type === "bootstrap_profile"
    ? { id: task.id, type: scope, max_items: task.max_items_per_scope,
      max_scroll_rounds: task.max_scroll_rounds,
      max_stagnant_scroll_rounds: task.max_stagnant_scroll_rounds }
    : task;
}

export function computeTiktokTaskTimeoutMs(task: TiktokAccountTask): number {
  if (task.type === "bootstrap_profile") {
    return Math.min(MAX_TASK_TIMEOUT_MS,
      task.scopes.length * computeTiktokTaskTimeoutMs(scopeTask(task, task.scopes[0])));
  }
  const rounds = taskLimit(task, "max_scroll_rounds");
  const scrollBudget = rounds === undefined ? 0 : Math.max(0, Math.floor(rounds));
  return Math.min(
    Math.max(BASE_TIMEOUT_MS, BASE_TIMEOUT_MS + scrollBudget * PER_ROUND_TIMEOUT_MS),
    MAX_TASK_TIMEOUT_MS,
  );
}

export function buildTiktokExecuteMessageData(task: TiktokScopeTask): Record<string, unknown> {
  return {
    task_id: task.id,
    scope: task.type,
    max_items: Math.min(DEFAULT_MAX_ITEMS, Math.max(1, Math.floor(taskLimit(task, "max_items") ?? DEFAULT_MAX_ITEMS))),
    max_scroll_rounds: taskLimit(task, "max_scroll_rounds") ?? DEFAULT_SCROLL_ROUNDS,
    max_stagnant_scroll_rounds:
      taskLimit(task, "max_stagnant_scroll_rounds") ?? DEFAULT_STAGNANT_ROUNDS,
  };
}

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------

let taskInFlight = false;
let pollInFlight: Promise<void> | null = null;
let taskTabId: number | null = null;
let ownsTaskTab = false;
let taskTimeoutId: ReturnType<typeof setTimeout> | null = null;
let taskDeadlineMs: number | null = null;
let taskIdleDeadlineMs: number | null = null;
let currentTask: TiktokAccountTask | null = null;
let scopeResults: Partial<Record<TiktokScope, TiktokScopeResult>> = {};
let scopeResultMutation: Promise<void> = Promise.resolve();
let failureInFlight: Promise<void> | null = null;

interface TaskProgress {
  task_id: string;
  scope: TiktokScope;
  max_items: number;
  max_scroll_rounds: number;
  max_stagnant_scroll_rounds: number;
}

let progress: TaskProgress | null = null;

// ---------------------------------------------------------------------------
// Network + durable pending result (contract [task] retry)
// ---------------------------------------------------------------------------

async function fetchNextTask(): Promise<TiktokTask | null> {
  try {
    const resp = await authenticatedFetch(await apiUrl("/sources/tiktok/next-task"));
    if (resp.status === 204) return null; // no pending task
    if (!resp.ok) return null;
    const payload: unknown = await resp.json();
    if (isValidTiktokTask(payload)) return payload;
    // GET /next-task atomically claimed whatever this was. A payload that
    // is neither a scope task nor a well-formed native_save task (native
    // save IS supported; this catches future unknown shapes) would strand
    // the backend lease in in_progress — fail it explicitly so the queue
    // recovers instead of waiting out the stale-claim timeout.
    const id = (payload as { id?: unknown } | null)?.id;
    if (typeof id === "string" && id) {
      await postTiktokTaskResult({
        task_id: id,
        status: "failed",
        error: "unsupported_task_type",
      }).catch(() => {});
    }
    return null;
  } catch {
    return null;
  }
}

export interface TiktokTaskResultResponse {
  ok: boolean;
  status: number;
}

export interface TiktokTaskResultTransport {
  resolveUrl: (path: string) => Promise<string>;
  fetch: (input: string, init: RequestInit) => Promise<TiktokTaskResultResponse>;
  sleep: (delayMs: number) => Promise<void>;
}

export interface TiktokTaskResultRetryOptions {
  maxAttempts?: number;
  baseDelayMs?: number;
  requestTimeoutMs?: number;
}

const TASK_RESULT_MAX_ATTEMPTS = 3;
const TASK_RESULT_REQUEST_TIMEOUT_MS = 10_000;
const TASK_RESULT_RETRY_BASE_DELAY_MS = 250;
const TASK_RESULT_RETRY_MAX_DELAY_MS = 2_000;

function delay(delayMs: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

const TIKTOK_TASK_RESULT_TRANSPORT: TiktokTaskResultTransport = {
  resolveUrl: apiUrl,
  fetch: authenticatedFetch,
  sleep: delay,
};

function localStorageArea(): chrome.storage.StorageArea | null {
  try {
    return typeof chrome === "undefined" ? null : chrome.storage?.local ?? null;
  } catch {
    return null;
  }
}

/**
 * The pending slot holds the exact canonical JSON body of the oldest
 * unacknowledged task-result callback and a FIFO tail. Persisted BEFORE the POST so a
 * worker death between persist and POST still replays the payload on
 * the next wake. Cleared on a 2xx ACK or an explicit 4xx terminal
 * rejection (retrying an identical body cannot change a client error).
 * Only task identity + normalized rows live here — never raw response
 * bodies or Cookie/CSRF material (contract [task] buffer clause).
 */
export interface PendingTiktokTaskResult {
  body: string;
  queued_at_ms: number;
  /** FIFO tail; migration accepts the former single-record shape. */
  following?: PendingTiktokTaskResult[];
}

async function readPendingResult(): Promise<PendingTiktokTaskResult | null> {
  const storage = localStorageArea();
  if (!storage) return null;
  try {
    const raw: unknown = (await storage.get(TIKTOK_PENDING_RESULT_LOCAL_KEY))[
      TIKTOK_PENDING_RESULT_LOCAL_KEY
    ];
    if (typeof raw !== "object" || raw === null) return null;
    const record = raw as Record<string, unknown>;
    if (typeof record.body !== "string" || !record.body) return null;
    return {
      body: record.body,
      following: Array.isArray(record.following)
        ? record.following.filter((entry): entry is PendingTiktokTaskResult =>
          !!entry && typeof entry.body === "string" && !!entry.body)
        : [],
      queued_at_ms:
        typeof record.queued_at_ms === "number" && Number.isFinite(record.queued_at_ms)
          ? record.queued_at_ms
          : 0,
    };
  } catch (error) {
    throw new Error("tiktok_result_storage_read_failed", { cause: error });
  }
}

let resultDelivery: Promise<void> = Promise.resolve();

function serializeResultDelivery(operation: () => Promise<void>): Promise<void> {
  const running = resultDelivery.then(operation, operation);
  resultDelivery = running.catch(() => {});
  return running;
}

async function savePendingQueue(queue: PendingTiktokTaskResult[]): Promise<void> {
  const storage = localStorageArea();
  if (!storage) throw new Error("tiktok_result_storage_unavailable");
  // A storage failure must stop lifecycle progress: RAM is not a durable ACK.
  if (!queue.length) await storage.remove(TIKTOK_PENDING_RESULT_LOCAL_KEY);
  else await storage.set({ [TIKTOK_PENDING_RESULT_LOCAL_KEY]: {
    ...queue[0], following: queue.slice(1).map(({ body, queued_at_ms }) => ({ body, queued_at_ms })),
  } });
}

async function pendingQueue(): Promise<PendingTiktokTaskResult[]> {
  const head = await readPendingResult();
  if (!head) return [];
  return [head, ...(head.following ?? [])].map(({ body, queued_at_ms }) => ({ body, queued_at_ms }));
}

async function drainPendingQueue(
  transport: TiktokTaskResultTransport,
  options: TiktokTaskResultRetryOptions,
): Promise<void> {
  const queue = await pendingQueue();
  while (queue.length) {
    await postRawTaskResult(queue[0].body, transport, options);
    queue.shift();
    await savePendingQueue(queue);
  }
}

/** Bound URL resolution and POST so a stalled request cannot hold the FIFO forever. */
async function postResultAttempt(
  body: string,
  transport: TiktokTaskResultTransport,
  requestedTimeout?: number,
): Promise<TiktokTaskResultResponse> {
  const timeoutMs = typeof requestedTimeout === "number" && Number.isFinite(requestedTimeout)
    ? Math.max(1, Math.min(TASK_RESULT_REQUEST_TIMEOUT_MS, requestedTimeout))
    : TASK_RESULT_REQUEST_TIMEOUT_MS;
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      (async () => {
        const url = await transport.resolveUrl("/sources/tiktok/task-result");
        // A late resolver must not start another POST after this attempt ended.
        if (controller.signal.aborted) throw new Error("task_result_request_timeout");
        return transport.fetch(url, {
          method: "POST", headers: { "content-type": "application/json" }, body,
          signal: controller.signal,
        });
      })(),
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(new Error("task_result_request_timeout"));
        }, timeoutMs);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

async function postRawTaskResult(
  body: string,
  transport: TiktokTaskResultTransport,
  options: TiktokTaskResultRetryOptions,
): Promise<"acked" | "rejected"> {
  const maxAttempts = Math.max(
    1,
    Math.min(5, Math.floor(options.maxAttempts ?? TASK_RESULT_MAX_ATTEMPTS)),
  );
  const baseDelayMs = Math.max(
    0,
    Math.min(
      TASK_RESULT_RETRY_MAX_DELAY_MS,
      Math.floor(options.baseDelayMs ?? TASK_RESULT_RETRY_BASE_DELAY_MS),
    ),
  );
  let lastFailure = "unknown";

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await postResultAttempt(body, transport, options.requestTimeoutMs);
      if (response.ok) return "acked";
      if (response.status === 409 || response.status === 422) {
        // Explicit terminal rejection — the backend refused this exact
        // canonical payload; bounded retries cannot change that.
        return "rejected";
      }
      lastFailure = `HTTP ${response.status}`;
    } catch (error) {
      lastFailure = String(error);
    }

    if (attempt < maxAttempts) {
      const retryDelayMs = Math.min(
        TASK_RESULT_RETRY_MAX_DELAY_MS,
        baseDelayMs * 2 ** (attempt - 1),
      );
      await transport.sleep(retryDelayMs);
    }
  }
  throw new Error(`tiktok_task_result_unacknowledged: ${lastFailure}`);
}

/**
 * Deliver one idempotent task callback, persisting the canonical body
 * until the backend ACKs. Throws when the bounded retry window expired
 * and the payload remains pending — callers decide lifecycle cleanup,
 * the pending slot survives either way and is flushed on the next wake.
 */
export async function postTiktokTaskResult(
  result: TiktokTaskCallbackResult,
  transport: TiktokTaskResultTransport = TIKTOK_TASK_RESULT_TRANSPORT,
  options: TiktokTaskResultRetryOptions = {},
): Promise<void> {
  return serializeResultDelivery(async () => {
    const body = JSON.stringify(result);
    const queue = await pendingQueue();
    if (!queue.some((entry) => entry.body === body)) {
      queue.push({ body, queued_at_ms: Date.now() });
      await savePendingQueue(queue);
    }
    await drainPendingQueue(transport, options);
  });
}

/** Retry the entire FIFO on every wake, before admitting another task. */
export async function flushPendingTiktokTaskResult(
  transport: TiktokTaskResultTransport = TIKTOK_TASK_RESULT_TRANSPORT,
): Promise<void> {
  await serializeResultDelivery(async () => {
    try {
      await drainPendingQueue(transport, { maxAttempts: 1 });
    } catch {
      // Persisted FIFO remains authoritative, including the unsent terminal.
    }
  });
}

// ---------------------------------------------------------------------------
// Active-task persistence (event page / service-worker recycling)
// ---------------------------------------------------------------------------

export interface PersistedTiktokTaskState {
  task: TiktokAccountTask;
  scope_results?: Partial<Record<TiktokScope, TiktokScopeResult>>;
  task_tab_id: number | null;
  owns_task_tab: boolean;
  deadline_ms: number;
  /** Missing old records migrate to their original absolute deadline. */
  idle_deadline_ms?: number;
  progress: TaskProgress | null;
}

let sessionStateMutation: Promise<void> = Promise.resolve();
let tiktokTaskRecoveryPromise: Promise<void> | null = null;

function sessionStorageArea(): chrome.storage.StorageArea | null {
  try {
    return typeof chrome === "undefined" ? null : chrome.storage?.session ?? chrome.storage?.local ?? null;
  } catch {
    return null;
  }
}

function serializedSessionMutation(mutation: () => Promise<void>): Promise<void> {
  const current = sessionStateMutation.then(mutation, mutation);
  sessionStateMutation = current.catch(() => {});
  return current;
}

function persistActiveTaskState(): Promise<void> {
  return serializedSessionMutation(async () => {
    const storage = sessionStorageArea();
    if (!storage || !currentTask) return;
    const record: PersistedTiktokTaskState = {
      task: currentTask,
      scope_results: scopeResults,
      task_tab_id: taskTabId,
      owns_task_tab: ownsTaskTab,
      deadline_ms: taskDeadlineMs ?? 0,
      idle_deadline_ms: taskIdleDeadlineMs ?? taskDeadlineMs ?? 0,
      progress,
    };
    await storage.set({ [TIKTOK_TASK_STATE_SESSION_KEY]: record });
  });
}

function clearPersistedTaskState(): Promise<void> {
  return serializedSessionMutation(async () => {
    const storage = sessionStorageArea();
    if (!storage) return;
    try {
      await storage.remove(TIKTOK_TASK_STATE_SESSION_KEY);
    } catch {
      // The in-memory lifecycle is already terminal for this worker.
    }
  });
}

function parsePersistedTaskState(value: unknown): PersistedTiktokTaskState | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  if (!isValidTiktokTask(record.task) || record.task.type === "native_save") return null;
  const task = record.task;
  if (typeof record.deadline_ms !== "number" || !Number.isFinite(record.deadline_ms)) {
    return null;
  }
  const tabId = record.task_tab_id ?? null;
  if (tabId !== null && (typeof tabId !== "number" || !Number.isInteger(tabId))) return null;
  const progressRecord = record.progress;
  if (progressRecord !== null && progressRecord !== undefined) {
    if (
      typeof progressRecord !== "object" ||
      (progressRecord as { task_id?: unknown }).task_id !== task.id ||
      !taskScopes(task).includes((progressRecord as { scope?: TiktokScope }).scope as TiktokScope)
    ) {
      return null;
    }
  }
  return {
    task,
    scope_results: record.scope_results as Partial<Record<TiktokScope, TiktokScopeResult>> | undefined,
    task_tab_id: tabId,
    owns_task_tab: record.owns_task_tab === true,
    deadline_ms: record.deadline_ms,
    idle_deadline_ms: typeof record.idle_deadline_ms === "number" && Number.isFinite(record.idle_deadline_ms)
      ? Math.min(record.idle_deadline_ms, record.deadline_ms) : record.deadline_ms,
    progress: (progressRecord as TaskProgress | null) ?? null,
  };
}

function scheduleTaskTimeoutAlarm(deadlineMs: number): void {
  if (typeof chrome === "undefined" || !chrome.alarms?.create) return;
  try {
    void chrome.alarms.create(TASK_TIMEOUT_ALARM_NAME, { when: deadlineMs });
  } catch {
    // The in-process timer still covers a live worker.
  }
}

function armTaskDeadline(deadlineMs: number): void {
  taskDeadlineMs = deadlineMs;
  // The one-shot alarm is the authoritative backstop: it survives the
  // service worker being unloaded. The setTimeout below is only the
  // exact-timing fast path while this worker stays alive.
  const nextDeadline = Math.min(deadlineMs, taskIdleDeadlineMs ?? deadlineMs);
  scheduleTaskTimeoutAlarm(nextDeadline);
  if (taskTimeoutId !== null) {
    clearTimeout(taskTimeoutId);
    taskTimeoutId = null;
  }
  taskTimeoutId = setTimeout(() => {
    taskTimeoutId = null;
    void failActiveTask(deadlineError());
  }, Math.max(0, nextDeadline - Date.now()));
}

function deadlineError(): string {
  return taskDeadlineMs !== null && Date.now() >= taskDeadlineMs ? "task_timeout" : "task_idle_timeout";
}

function markBusinessProgress(): void {
  if (taskDeadlineMs === null) return;
  taskIdleDeadlineMs = Math.min(taskDeadlineMs, Date.now() + TASK_IDLE_TIMEOUT_MS);
  armTaskDeadline(taskDeadlineMs);
}

async function checkpointRowsBeforeFailure(task: TiktokAccountTask): Promise<void> {
  if (!progress || scopeResults[progress.scope]) return;
  try {
    const storage = localStorageArea();
    if (!storage) return;
    const raw = (await storage.get(TIKTOK_CONTENT_SCOPE_KEY))[TIKTOK_CONTENT_SCOPE_KEY] as {
      message?: { task_id?: string; scope?: TiktokScope };
      accepted_items?: TiktokBootstrapItem[];
      viewer_handle?: string; viewer_sec_uid?: string;
    } | undefined;
    if (!raw || raw.message?.task_id !== task.id || raw.message.scope !== progress.scope ||
        !Array.isArray(raw.accepted_items) || !raw.accepted_items.length || !raw.viewer_handle) return;
    const identity = accountIdentity();
    if ((identity.viewer_handle && identity.viewer_handle.toLowerCase() !== raw.viewer_handle.toLowerCase()) ||
        (identity.viewer_sec_uid && raw.viewer_sec_uid && identity.viewer_sec_uid !== raw.viewer_sec_uid)) return;
    scopeResults[progress.scope] = boundedScopeResult({
      task_id: task.id, scope: progress.scope, status: "degraded", error: "scope_interrupted",
      items: raw.accepted_items, scope_count: raw.accepted_items.length,
      viewer_handle: raw.viewer_handle, ...(raw.viewer_sec_uid ? { viewer_sec_uid: raw.viewer_sec_uid } : {}),
    }, progress);
    await persistActiveTaskState();
  } catch { /* Previously accepted dispatcher rows remain available in its snapshot. */ }
}

function failActiveTask(error: string): Promise<void> {
  if (failureInFlight) return failureInFlight;
  const running = (async () => {
    const task = currentTask;
    if (!task) return;
    try {
      await checkpointRowsBeforeFailure(task);
      await postTiktokTaskResult(accountResult(task, error));
      await cleanupTask();
    } catch {
      // Keep lifecycle and the durable FIFO until another wake retries it.
    }
  })();
  failureInFlight = running;
  void running.finally(() => { if (failureInFlight === running) failureInFlight = null; });
  return running;
}

async function recordedTabAlive(tabId: number | null): Promise<boolean> {
  if (tabId === null) return false;
  try {
    await chrome.tabs.get(tabId);
    return true;
  } catch {
    return false;
  }
}

async function restorePersistedTaskState(record: PersistedTiktokTaskState): Promise<void> {
  const tabAlive = await recordedTabAlive(record.task_tab_id);
  currentTask = record.task;
  taskTabId = tabAlive ? record.task_tab_id : null;
  ownsTaskTab = record.owns_task_tab && tabAlive;
  progress = record.progress;
  scopeResults = record.scope_results ?? {};
  taskInFlight = true;
  tryAcquireDispatcherMutex(MUTEX_LABEL);
  taskIdleDeadlineMs = record.idle_deadline_ms ?? record.deadline_ms;
  armTaskDeadline(record.deadline_ms);
  if (Date.now() >= Math.min(record.deadline_ms, taskIdleDeadlineMs)) {
    await failActiveTask(deadlineError());
    return;
  }
  if (record.owns_task_tab && !tabAlive) {
    // The task tab is gone; nothing can deliver further results, so
    // settle immediately instead of burning the remaining deadline.
    await failActiveTask("task_tab_closed");
  } else if (progress && scopeResults[progress.scope]) {
    await continueAccountTask();
  } else if (tabAlive) {
    sendScopeExecuteMessage();
  }
}

async function sweepOrphanTiktokTaskTabs(activeTabId: number | null): Promise<void> {
  if (typeof chrome === "undefined" || !chrome.tabs?.query || !chrome.tabs?.remove) return;
  let tabs: chrome.tabs.Tab[];
  try {
    tabs = await chrome.tabs.query({ url: ["*://*.tiktok.com/*"] });
  } catch {
    return;
  }
  const queryMarker = `${TIKTOK_TASK_MARKER}=1`;
  for (const tab of tabs) {
    if (tab.id === undefined || tab.id === activeTabId) continue;
    const url = tab.url ?? tab.pendingUrl ?? "";
    if (!url.includes(queryMarker)) continue;
    try {
      await chrome.tabs.remove(tab.id);
    } catch {
      // Orphan sweep is best-effort; the recorded-tab path stays authoritative.
    }
  }
}

async function recoverTiktokTaskState(): Promise<void> {
  // The pending-result slot is flushed first: a previous worker may have
  // died between persist and ACK, and the backend's first-final-wins
  // makes the replay idempotent.
  await flushPendingTiktokTaskResult();
  const storage = sessionStorageArea();
  if (storage && !taskInFlight && currentTask === null) {
    let record: PersistedTiktokTaskState | null = null;
    try {
      record = parsePersistedTaskState(
        (await storage.get(TIKTOK_TASK_STATE_SESSION_KEY))[TIKTOK_TASK_STATE_SESSION_KEY],
      );
    } catch {
      record = null;
    }
    if (record) await restorePersistedTaskState(record);
  }
  await sweepOrphanTiktokTaskTabs(taskTabId);
}

/**
 * One MV3-lifetime barrier shared by startup, poll wakes, timeout
 * alarms, and the TIKTOK_SCOPE_RESULT handler. A freshly woken worker
 * flushes the pending slot and restores the persisted in-flight task
 * before any of those paths inspect module state.
 */
export function ensureTiktokTaskRecovery(): Promise<void> {
  tiktokTaskRecoveryPromise ??= recoverTiktokTaskState().catch(() => {});
  return tiktokTaskRecoveryPromise;
}

export function resetTiktokTaskStateForTest(): void {
  if (taskTimeoutId !== null) {
    clearTimeout(taskTimeoutId);
    taskTimeoutId = null;
  }
  taskDeadlineMs = null;
  taskIdleDeadlineMs = null;
  taskTabId = null;
  ownsTaskTab = false;
  currentTask = null;
  scopeResults = {};
  progress = null;
  taskInFlight = false;
  tiktokTaskRecoveryPromise = null;
  scopeResultMutation = Promise.resolve();
  failureInFlight = null;
  resultDelivery = Promise.resolve();
  sessionStateMutation = Promise.resolve();
  releaseDispatcherMutex(MUTEX_LABEL);
}

async function cleanupTask(): Promise<void> {
  const completedTaskId = currentTask?.id;
  if (taskTimeoutId !== null) {
    clearTimeout(taskTimeoutId);
    taskTimeoutId = null;
  }
  taskDeadlineMs = null;
  taskIdleDeadlineMs = null;
  if (typeof chrome !== "undefined" && chrome.alarms?.clear) {
    try {
      void chrome.alarms.clear(TASK_TIMEOUT_ALARM_NAME);
    } catch {
      // The alarm may already have fired; clearing stays best-effort.
    }
  }
  if (ownsTaskTab && taskTabId !== null) {
    try {
      await chrome.tabs.remove(taskTabId);
    } catch {
      // Tab may already be closed; ignore.
    }
  }
  if (completedTaskId) {
    try {
      const storage = localStorageArea();
      const record = storage ? (await storage.get(TIKTOK_CONTENT_SCOPE_KEY))[TIKTOK_CONTENT_SCOPE_KEY] as
        { message?: { task_id?: string } } | undefined : undefined;
      if (record?.message?.task_id === completedTaskId) await storage!.remove(TIKTOK_CONTENT_SCOPE_KEY);
    } catch { /* Bounded content checkpoint expiry remains the fallback. */ }
  }
  await clearPersistedTaskState();
  taskTabId = null;
  ownsTaskTab = false;
  currentTask = null;
  scopeResults = {};
  progress = null;
  taskInFlight = false;
  releaseDispatcherMutex(MUTEX_LABEL);
}

function armTaskTimeout(task: TiktokAccountTask): void {
  const now = Date.now();
  const absoluteDeadline = now + computeTiktokTaskTimeoutMs(task);
  taskIdleDeadlineMs = Math.min(absoluteDeadline, now + TASK_IDLE_TIMEOUT_MS);
  armTaskDeadline(absoluteDeadline);
  void persistActiveTaskState().catch(() => { void failActiveTask("task_state_persist_failed"); });
}

// ---------------------------------------------------------------------------
// Scope execution driver
// ---------------------------------------------------------------------------

/**
 * Wait for tab.status === "complete" then run the callback. Cleans
 * itself up on the first complete signal so intra-page navigations
 * don't re-trigger the handshake.
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
  void chrome.tabs
    .get(tabId)
    .then((tab) => {
      if (tab.status === "complete") runOnce();
    })
    .catch(() => {
      // Tab may close between create and readiness probing. The timeout
      // path reports the failure if no completion event arrives.
    });
}

/** Deliver through slow content-script registration without retrying another scope. */
function sendScopeExecuteMessage(): void {
  if (!progress || taskTabId === null) return;
  const sent = progress;
  const tabId = taskTabId;
  const deliveryDeadline = Date.now() + 20_000;
  const stillCurrent = (): boolean => progress === sent && taskTabId === tabId && taskInFlight;
  const attempt = async (): Promise<void> => {
    if (!stillCurrent()) return;
    let ackTimer: ReturnType<typeof setTimeout> | undefined;
    try {
      const ack = await Promise.race([
        chrome.tabs.sendMessage(tabId, {
          action: "TIKTOK_SCOPE_EXECUTE",
          data: { ...sent },
        }),
        new Promise<never>((_resolve, reject) => {
          ackTimer = setTimeout(() => reject(new Error("scope_acceptance_timeout")), 1_000);
        }),
      ]);
      if (ack?.accepted !== true || ack.task_id !== sent.task_id || ack.scope !== sent.scope) {
        throw new Error("scope_not_accepted");
      }
    } catch {
      if (!stillCurrent()) return;
      // The document-complete fallback may run before the content script
      // exists. Retrying the identical scope is safe: its persistent token
      // makes the content executor idempotent across full navigation.
      if (Date.now() < deliveryDeadline) {
        setTimeout(() => { void attempt(); }, 500);
        return;
      }
      await handleTiktokScopeResult({
        task_id: sent.task_id,
        scope: sent.scope,
        items: [],
        scope_count: 0,
        status: "failed",
        error: "sendMessage_failed",
      });
    } finally {
      if (ackTimer !== undefined) clearTimeout(ackTimer);
    }
  };
  void attempt();
}

export interface TiktokNativeSaveResultTransport {
  resolveUrl: (path: string) => Promise<string>;
  fetch: (input: string, init: RequestInit) => Promise<TiktokTaskResultResponse>;
}

const TIKTOK_NATIVE_SAVE_RESULT_TRANSPORT: TiktokNativeSaveResultTransport = {
  resolveUrl: apiUrl,
  fetch: authenticatedFetch,
};

export async function postTiktokNativeSaveResult(
  result: NativeSaveResult,
  transport: TiktokNativeSaveResultTransport = TIKTOK_NATIVE_SAVE_RESULT_TRANSPORT,
): Promise<void> {
  // Shared runner sees a real ACK/error; an HTTP failure is never success.
  await postTiktokTaskResult(result, { ...transport, sleep: delay });
}

export interface TiktokNativeSaveDispatchDependencies {
  run: (
    task: NativeSaveTask,
    platformSlug: "tiktok",
    postResult: (result: NativeSaveResult) => Promise<void>,
  ) => Promise<void>;
  postResult: (result: NativeSaveResult) => Promise<void>;
}

/** Behavior seam used by executeTask and tests to keep native-result closure explicit. */
export async function dispatchTiktokNativeSaveTask(
  task: NativeSaveTask,
  dependencies: TiktokNativeSaveDispatchDependencies,
): Promise<void> {
  await dependencies.run(task, "tiktok", dependencies.postResult);
}

const TIKTOK_NATIVE_SAVE_DISPATCH_DEPENDENCIES: TiktokNativeSaveDispatchDependencies = {
  run: runNativeSaveTask,
  postResult: postTiktokNativeSaveResult,
};

export async function executeTask(
  task: TiktokTask,
  nativeDependencies: TiktokNativeSaveDispatchDependencies = TIKTOK_NATIVE_SAVE_DISPATCH_DEPENDENCIES,
  mutexAlreadyHeld: boolean = false,
): Promise<TiktokTaskExecutionDisposition> {
  if (task.type === "native_save") {
    if (taskInFlight) return "declined";
    taskInFlight = true;
    try {
      await dispatchTiktokNativeSaveTask(task, nativeDependencies);
    } finally {
      taskInFlight = false;
    }
    return "accepted";
  }
  if (taskInFlight) return "declined";
  // Direct callers acquire here. pollTiktokTaskOnce acquires before
  // claiming from the backend so a busy sibling dispatcher cannot strand
  // an already-claimed TikTok task in ``in_progress``.
  if (!mutexAlreadyHeld && !tryAcquireDispatcherMutex(MUTEX_LABEL)) return "declined";
  taskInFlight = true;
  currentTask = task;

  scopeResults = {};
  setScopeProgress(task, taskScopes(task)[0]);

  // Contract extension.background = true: account tasks run in a
  // background tab, never stealing browser focus.
  let tab: chrome.tabs.Tab;
  try {
    tab = await createTaskTab({ url: buildTiktokTaskUrl(task), active: false });
  } catch (err) {
    armTaskTimeout(task);
    await postTiktokTaskResult({
      task_id: task.id,
      status: "failed",
      error: "tab_create_failed",
      debug: { tab_create_error: String(err) },
    });
    await cleanupTask();
    return "accepted";
  }
  taskTabId = tab.id ?? null;
  ownsTaskTab = true;
  armTaskTimeout(task);
  if (taskTabId === null) {
    await postTiktokTaskResult({
      task_id: task.id,
      status: "failed",
      error: "tab_id_unknown",
    });
    await cleanupTask();
    return "accepted";
  }
  onTabReady(taskTabId, sendScopeExecuteMessage, { fallbackMs: 12_000 });
  return "accepted";
}

function setScopeProgress(task: TiktokAccountTask, scope: TiktokScope): void {
  progress = buildTiktokExecuteMessageData(scopeTask(task, scope)) as unknown as TaskProgress;
}

function accountResult(task: TiktokAccountTask, error?: string): TiktokTaskResult {
  const scopes = taskScopes(task);
  const results = scopes.map((scope) => scopeResults[scope]);
  const videos = results.flatMap((result) => result?.items ?? []);
  const complete = !error && results.every((result) => result &&
    (result.status === "ok" || result.status === "empty"));
  return {
    task_id: task.id,
    ...accountIdentity(),
    status: complete ? "ok" : videos.length ? "degraded" : "failed",
    videos,
    scope_counts: Object.fromEntries(scopes.map((scope) => [scope, scopeResults[scope]?.items.length ?? 0])),
    ...(!complete ? { error: error || results.find((result) => result?.error)?.error || "scope_failed" } : {}),
    debug: {
      scope_statuses: Object.fromEntries(scopes.map((scope) => [scope, scopeResults[scope]?.status ?? "failed"])),
      scope_complete: Object.fromEntries(scopes.map((scope) => [scope,
        ["ok", "empty"].includes(scopeResults[scope]?.status ?? "")])),
      scope_errors: Object.fromEntries(scopes.map((scope) => [scope, scopeResults[scope]?.error ?? error ?? ""])),
    },
  };
}

function accountIdentity(): { viewer_handle?: string; viewer_sec_uid?: string } {
  // Pin one representation at first projected scope. A later SSR-only uid
  // must not change the backend account partition after handle-only binding.
  const first = Object.values(scopeResults).find((result) => result?.status !== "failed" && result?.viewer_handle);
  return first ? { viewer_handle: first.viewer_handle,
    ...(first.viewer_sec_uid ? { viewer_sec_uid: first.viewer_sec_uid } : {}) } : {};
}

/** Freeze only accepted scope rows; callback counts cannot enlarge the claim. */
function boundedScopeResult(result: TiktokScopeResult, claim: TaskProgress): TiktokScopeResult {
  const seen = new Set<string>();
  const items = (Array.isArray(result.items) ? result.items : []).filter((item) => {
    if (!item || item.scope !== claim.scope) return false;
    const id = claim.scope === "tt_follow" ? item.author_handle : item.video_id;
    if (!id || typeof id !== "string" || seen.has(id) || seen.size >= claim.max_items) return false;
    seen.add(id);
    return true;
  });
  const clipped = items.length < (Array.isArray(result.items) ? result.items.length : 0);
  return { ...result, items, scope_count: items.length,
    status: clipped || (result.status === "failed" && items.length) ? "degraded" : result.status,
    ...(clipped ? { error: "scope_rows_outside_claim" } : {}),
  };
}

async function continueAccountTask(): Promise<void> {
  const task = currentTask;
  if (!task) return;
  const next = taskScopes(task).find((scope) => !scopeResults[scope]);
  if (!next) {
    // Final carries the full immutable aggregate, so backend recovery never
    // depends on an earlier partial callback having arrived.
    await postTiktokTaskResult(accountResult(task));
    await cleanupTask();
    return;
  }
  setScopeProgress(task, next);
  await persistActiveTaskState();
  if (taskTabId === null) {
    await failActiveTask("task_tab_closed");
    return;
  }
  try {
    // Clear previous profile tab/modal without touching any user's tab.
    await chrome.tabs.update(taskTabId, { url: buildTiktokTaskUrl(task) });
    onTabReady(taskTabId, sendScopeExecuteMessage, { fallbackMs: 12_000 });
  } catch {
    await failActiveTask("scope_navigation_failed");
  }
}

/** Serialize scope completions, preserving rows across failures and worker wakes. */
export function handleTiktokScopeResult(result: TiktokScopeResult, senderTabId?: number): Promise<void> {
  const running = scopeResultMutation.then(async () => {
    await ensureTiktokTaskRecovery();
    // Runtime listeners pass the actual sender tab. Internal failure reports
    // omit it; a different TikTok/user tab cannot inject scope results.
    if (senderTabId !== undefined && senderTabId !== taskTabId) return;
    if (!progress || !currentTask || result.task_id !== progress.task_id) return;
    if (result.scope !== progress.scope || scopeResults[result.scope]) return;
    const identity = accountIdentity();
    if ((identity.viewer_handle && result.viewer_handle &&
        identity.viewer_handle.toLowerCase() !== result.viewer_handle.toLowerCase()) ||
        (identity.viewer_sec_uid && result.viewer_sec_uid && identity.viewer_sec_uid !== result.viewer_sec_uid)) {
      await failActiveTask("account_identity_conflict");
      return;
    }
    const accepted = boundedScopeResult(result, progress);
    scopeResults[result.scope] = accepted;
    markBusinessProgress();
    await persistActiveTaskState();
    if (accepted.items.length || accepted.status !== "failed") {
      // A transport outage leaves this partial in the FIFO; the final is
      // appended behind it, never overwrites it. Scope collection may proceed.
      await postTiktokTaskResult({
        task_id: result.task_id,
        ...accountIdentity(),
        status: "partial",
        videos: accepted.items,
        scope_counts: { [result.scope]: accepted.items.length },
        debug: { scope: result.scope, scope_status: accepted.status },
      }).catch(() => {});
    }
    await continueAccountTask();
  });
  scopeResultMutation = running.catch(() => {});
  return running;
}

// ---------------------------------------------------------------------------
// Polling & alarm wiring
// ---------------------------------------------------------------------------

async function reportDeclinedTask(task: TiktokTask): Promise<void> {
  if (task.type === "native_save") {
    await postTiktokTaskResult({
      task_id: task.id,
      item_key: task.item_key,
      status: "failed",
      error_code: "native_save_failed",
      error_message: "dispatcher_busy_after_claim",
    });
    return;
  }
  await postTiktokTaskResult({
    task_id: task.id,
    status: "failed",
    error: "dispatcher_busy_after_claim",
  });
}

export interface TiktokTaskPollDependencies {
  ensureRecovery: () => Promise<void>;
  canExecute?: () => boolean;
  fetchTask: () => Promise<TiktokTask | null>;
  execute: (
    task: TiktokTask,
    mutexAlreadyHeld: boolean,
  ) => Promise<TiktokTaskExecutionDisposition>;
  reportDeclined: (task: TiktokTask) => Promise<void>;
}

const TIKTOK_TASK_POLL_DEPENDENCIES: TiktokTaskPollDependencies = {
  ensureRecovery: async () => {
    await ensureNativeSaveTaskRecovery();
    await ensureTiktokTaskRecovery();
  },
  canExecute: () => {
    try {
      return typeof globalThis.chrome?.tabs?.create === "function";
    } catch {
      return false;
    }
  },
  fetchTask: fetchNextTask,
  execute: async (task, mutexAlreadyHeld) =>
    executeTask(task, TIKTOK_NATIVE_SAVE_DISPATCH_DEPENDENCIES, mutexAlreadyHeld),
  reportDeclined: reportDeclinedTask,
};

export function pollTiktokTaskOnce(
  dependencies: TiktokTaskPollDependencies = TIKTOK_TASK_POLL_DEPENDENCIES,
): Promise<void> {
  if (pollInFlight) return pollInFlight;
  const running = (async () => {
    await dependencies.ensureRecovery();
    await flushPendingTiktokTaskResult();
    if (currentTask && taskScopes(currentTask).every((scope) => scopeResults[scope])) {
      // A prior final POST may have failed after its durable enqueue. Retry
      // the same aggregate and release the owned tab/mutex once acknowledged.
      await postTiktokTaskResult(accountResult(currentTask));
      await cleanupTask();
    }
    if (taskInFlight || await readPendingResult()) return;
    // A runtime-stream subscriber is not necessarily a fully-capable
    // browser extension worker. Never atomically claim a backend task
    // when this context cannot create the tab the task requires.
    if (dependencies.canExecute && !dependencies.canExecute()) return;

    // GET /next-task atomically changes pending -> in_progress. Take the
    // cross-source slot first so a busy sibling dispatcher can never make
    // us claim a task we then abandon without a tab or terminal callback.
    if (!tryAcquireDispatcherMutex(MUTEX_LABEL)) return;
    let releaseOnExit = true;
    try {
      const task = await dependencies.fetchTask();
      if (!task) return;
      if (task.type === "native_save") {
        // Native-save owns a separate durable runner/recovery lifecycle and
        // does not hold the scope-task mutex.
        releaseDispatcherMutex(MUTEX_LABEL);
        releaseOnExit = false;
        const disposition = await dependencies.execute(task, false);
        if (disposition === "declined") await dependencies.reportDeclined(task);
        return;
      }
      const disposition = await dependencies.execute(task, true);
      if (disposition === "declined") {
        await dependencies.reportDeclined(task);
        return;
      }
      // The task lifecycle now owns the mutex; cleanupTask releases it
      // after a result, timeout, or tab/message failure.
      releaseOnExit = false;
    } finally {
      if (releaseOnExit) releaseDispatcherMutex(MUTEX_LABEL);
    }
  })();
  pollInFlight = running;
  const clearPoll = (): void => {
    if (pollInFlight === running) pollInFlight = null;
  };
  void running.then(clearPoll, clearPoll);
  return running;
}

/**
 * Set up the tiktok task-poll alarm. Idempotent — chrome.alarms.create
 * with an existing name overwrites the schedule. Skip in non-extension
 * environments (node:test importing the module for pure-helper tests).
 *
 * Service-worker.ts owns the global chrome.alarms.onAlarm listener and
 * dispatches into handleTiktokTaskAlarm from there; don't register a
 * second listener here.
 */
export function startTiktokTaskPolling(): void {
  if (typeof chrome === "undefined" || !chrome.alarms) return;
  chrome.alarms.create(POLL_ALARM_NAME, {
    periodInMinutes: DEFAULT_POLL_INTERVAL_MS / 60_000,
  });
}

export function handleTiktokTaskAlarm(alarmName: string): void {
  if (alarmName === POLL_ALARM_NAME) {
    void pollTiktokTaskOnce().catch(() => {});
    return;
  }
  if (alarmName === TASK_TIMEOUT_ALARM_NAME) {
    void (async () => {
      await ensureTiktokTaskRecovery();
      if (!currentTask || taskDeadlineMs === null) return;
      if (Date.now() < Math.min(taskDeadlineMs, taskIdleDeadlineMs ?? taskDeadlineMs)) {
        // The browser clamped the one-shot alarm to its minimum delay;
        // re-arm the in-process fast path for the remaining budget.
        armTaskDeadline(taskDeadlineMs);
        return;
      }
      await failActiveTask(deadlineError());
    })().catch(() => {});
  }
}

/**
 * Trigger an immediate poll. Used by the runtime-stream WebSocket
 * handler when the backend broadcasts ``tiktok_task_available``.
 * Idempotent: pollTiktokTaskOnce short-circuits when a task is in
 * flight.
 */
export function pollTiktokTaskNow(): void {
  void pollTiktokTaskOnce().catch(() => {});
}
