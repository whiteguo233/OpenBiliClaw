/** Durable background dispatcher for read-only V2EX browser bootstrap tasks. */

import type {
  V2EXBootstrapItem,
  V2EXScope,
  V2EXScopeResult,
} from "../content/v2ex/task-executor.ts";
import {
  V2EX_TASK_TAB_PARAM,
  isV2EXTaskTabLocation,
} from "../content/v2ex/task-mode.ts";
import { apiUrl } from "../shared/backend-endpoint.ts";
import { authenticatedFetch } from "../shared/auth.ts";
import { createTaskTab } from "./task-tab.ts";

const MUTEX_STALE_MS = 6 * 60 * 1000;
const POLL_INTERVAL_MS = 60_000;
const POLL_ALARM_NAME = "openbiliclaw-v2ex-task-poll";
const RESULT_RETRY_ALARM_NAME = "openbiliclaw-v2ex-result-retry";
const SESSION_KEY = "openbiliclaw_v2ex_task_state_v1";
const IDLE_TIMEOUT_MS = 90_000;
const ABSOLUTE_TIMEOUT_MS = 12 * 60 * 1000;
const RESULT_RETRY_DELAY_MINUTES = 0.25;
const RESULT_MAX_ATTEMPTS = 3;
const RESULT_RETRY_BASE_MS = 250;
const RESULT_RETRY_MAX_MS = 2_000;
const MAX_WIRE_LIMIT = 10_000;
const DEFAULT_SCOPES: readonly V2EXScope[] = [
  "public_topics",
  "public_replies",
  "favorite_topics",
  "favorite_nodes",
];

function tryAcquireMutex(): boolean {
  const state = globalThis as unknown as {
    __OBC_DISPATCHER_MUTEX_HOLDER__?: string;
    __OBC_DISPATCHER_MUTEX_HELD_SINCE__?: number;
  };
  if (state.__OBC_DISPATCHER_MUTEX_HOLDER__) {
    if (Date.now() - (state.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ || 0) <= MUTEX_STALE_MS) {
      return false;
    }
  }
  state.__OBC_DISPATCHER_MUTEX_HOLDER__ = "v2ex";
  state.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = Date.now();
  return true;
}

function refreshMutex(): void {
  const state = globalThis as unknown as {
    __OBC_DISPATCHER_MUTEX_HOLDER__?: string;
    __OBC_DISPATCHER_MUTEX_HELD_SINCE__?: number;
  };
  if (state.__OBC_DISPATCHER_MUTEX_HOLDER__ === "v2ex") {
    state.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = Date.now();
  }
}

function releaseMutex(): void {
  const state = globalThis as unknown as {
    __OBC_DISPATCHER_MUTEX_HOLDER__?: string;
    __OBC_DISPATCHER_MUTEX_HELD_SINCE__?: number;
  };
  if (state.__OBC_DISPATCHER_MUTEX_HOLDER__ === "v2ex") {
    state.__OBC_DISPATCHER_MUTEX_HOLDER__ = undefined;
    state.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = undefined;
  }
}

export interface V2EXTask {
  id: string;
  type: "bootstrap_profile";
  scopes?: V2EXScope[];
  username?: string;
  max_topics?: number;
  max_replies?: number;
  max_favorite_topics?: number;
  max_pages_per_scope?: number;
}

export function isValidV2EXTask(value: unknown): value is V2EXTask {
  if (!value || typeof value !== "object") return false;
  const task = value as Record<string, unknown>;
  if (typeof task.id !== "string" || !task.id || task.type !== "bootstrap_profile") return false;
  if (task.username !== undefined && typeof task.username !== "string") return false;
  if (task.scopes !== undefined) {
    if (
      !Array.isArray(task.scopes)
      || task.scopes.length === 0
      || task.scopes.some((scope) => !DEFAULT_SCOPES.includes(scope as V2EXScope))
      || new Set(task.scopes).size !== task.scopes.length
    ) return false;
  }
  for (const key of [
    "max_topics",
    "max_replies",
    "max_favorite_topics",
    "max_pages_per_scope",
  ]) {
    const numberValue = task[key];
    if (
      numberValue !== undefined
      && (typeof numberValue !== "number" || !Number.isFinite(numberValue) || numberValue <= 0)
    ) return false;
  }
  return true;
}

function withTaskHash(url: string): string {
  return `${url}#${V2EX_TASK_TAB_PARAM}=1`;
}

export function v2exScopeUrl(scope: V2EXScope, username: string, page: number): string {
  const encoded = username ? encodeURIComponent(username) : "";
  if (scope === "public_topics") {
    return withTaskHash(encoded
      ? `https://www.v2ex.com/member/${encoded}?p=${Math.max(1, page)}`
      : "https://www.v2ex.com/");
  }
  if (scope === "public_replies") {
    if (!encoded) return withTaskHash("https://www.v2ex.com/");
    return withTaskHash(`https://www.v2ex.com/member/${encoded}/replies?p=${Math.max(1, page)}`);
  }
  if (scope === "favorite_topics") {
    return withTaskHash(`https://www.v2ex.com/my/topics?p=${Math.max(1, page)}`);
  }
  return withTaskHash(`https://www.v2ex.com/my/nodes?p=${Math.max(1, page)}`);
}

export interface V2EXTaskResultPayload {
  task_id: string;
  status: "ok" | "partial" | "empty" | "failed";
  items: V2EXBootstrapItem[];
  scope_counts: Record<string, number>;
  error?: string;
  debug?: Record<string, unknown>;
}

interface Progress {
  task: V2EXTask;
  scopes: V2EXScope[];
  scopeIndex: number;
  page: number;
  maxPages: number;
  username: string;
  observedUsername: string;
  items: Map<string, V2EXBootstrapItem>;
  scopeCounts: Record<string, number>;
  scopeComplete: Partial<Record<V2EXScope, boolean>>;
  scopeStatuses: Partial<Record<V2EXScope, V2EXScopeResult["status"]>>;
  failures: string[];
  identityRetryUsed: boolean;
  loggedIn: boolean;
  idleDeadlineAt: number;
  absoluteDeadlineAt: number;
}

interface PersistedState {
  version: 1;
  task: V2EXTask;
  scopes: V2EXScope[];
  scopeIndex: number;
  page: number;
  maxPages: number;
  username: string;
  observedUsername: string;
  items: V2EXBootstrapItem[];
  scopeCounts: Record<string, number>;
  scopeComplete: Partial<Record<V2EXScope, boolean>>;
  scopeStatuses: Partial<Record<V2EXScope, V2EXScopeResult["status"]>>;
  failures: string[];
  identityRetryUsed: boolean;
  loggedIn: boolean;
  idleDeadlineAt: number;
  absoluteDeadlineAt: number;
  tabId: number | null;
  pendingResult?: V2EXTaskResultPayload;
}

export interface V2EXScopePageDecision {
  continuePaging: boolean;
  complete: boolean;
  truncated: boolean;
}

/** Decide whether one read-only scope has supplied a complete collection. */
export function v2exScopePageDecision(
  scope: V2EXScope,
  page: number,
  maxPages: number,
  status: V2EXScopeResult["status"],
  itemCount: number,
  pageTruncated = false,
  hasNextPage: boolean | undefined = undefined,
): V2EXScopePageDecision {
  if (status !== "ok" && status !== "empty") {
    return { continuePaging: false, complete: false, truncated: false };
  }
  if (pageTruncated) {
    return { continuePaging: false, complete: false, truncated: true };
  }
  if (scope === "favorite_nodes") {
    return { continuePaging: false, complete: true, truncated: false };
  }
  if (status === "empty" && itemCount <= 0) {
    return { continuePaging: false, complete: true, truncated: false };
  }
  if (hasNextPage === false) {
    return { continuePaging: false, complete: true, truncated: false };
  }
  if (page < maxPages) {
    return { continuePaging: true, complete: false, truncated: false };
  }
  return { continuePaging: false, complete: false, truncated: true };
}

export interface V2EXTaskResultResponse {
  ok: boolean;
  status: number;
}

export interface V2EXTaskResultTransport {
  resolveUrl: (path: string) => Promise<string>;
  fetch: (input: string, init: RequestInit) => Promise<V2EXTaskResultResponse>;
  sleep: (delayMs: number) => Promise<void>;
}

export interface V2EXTaskResultRetryOptions {
  maxAttempts?: number;
  baseDelayMs?: number;
}

function delay(delayMs: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

const RESULT_TRANSPORT: V2EXTaskResultTransport = {
  resolveUrl: apiUrl,
  fetch: authenticatedFetch,
  sleep: delay,
};

/** POST one byte-identical callback until a 2xx ACK or the bounded attempt set ends. */
export async function postV2EXTaskResult(
  result: V2EXTaskResultPayload,
  transport: V2EXTaskResultTransport = RESULT_TRANSPORT,
  options: V2EXTaskResultRetryOptions = {},
): Promise<void> {
  const maxAttempts = Math.max(1, Math.min(5, Math.floor(options.maxAttempts ?? RESULT_MAX_ATTEMPTS)));
  const baseDelayMs = Math.max(
    0,
    Math.min(RESULT_RETRY_MAX_MS, Math.floor(options.baseDelayMs ?? RESULT_RETRY_BASE_MS)),
  );
  const body = JSON.stringify(result);
  let lastFailure = "unknown";
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await transport.fetch(await transport.resolveUrl("/sources/v2ex/task-result"), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
      });
      if (response.ok) return;
      lastFailure = `HTTP ${response.status}`;
    } catch (error) {
      lastFailure = String(error);
    }
    if (attempt < maxAttempts) {
      await transport.sleep(Math.min(RESULT_RETRY_MAX_MS, baseDelayMs * 2 ** (attempt - 1)));
    }
  }
  throw new Error(`v2ex_task_result_unacknowledged: ${lastFailure}`);
}

let inFlight = false;
let pollInFlight: Promise<void> | null = null;
let tabId: number | null = null;
let deadlineTimer: ReturnType<typeof setTimeout> | null = null;
let progress: Progress | null = null;
let pendingResult: V2EXTaskResultPayload | null = null;
let recoveryPromise: Promise<void> | null = null;
let storageMutation: Promise<void> = Promise.resolve();
let navigationGeneration = 0;
let resultMutation: Promise<void> = Promise.resolve();

function storageArea(): chrome.storage.StorageArea | null {
  try {
    return typeof chrome === "undefined" ? null : chrome.storage?.session ?? null;
  } catch {
    return null;
  }
}

function serializeState(): PersistedState | null {
  if (!progress) return null;
  return {
    version: 1,
    task: progress.task,
    scopes: [...progress.scopes],
    scopeIndex: progress.scopeIndex,
    page: progress.page,
    maxPages: progress.maxPages,
    username: progress.username,
    observedUsername: progress.observedUsername,
    items: [...progress.items.values()],
    scopeCounts: { ...progress.scopeCounts },
    scopeComplete: { ...progress.scopeComplete },
    scopeStatuses: { ...progress.scopeStatuses },
    failures: [...progress.failures],
    identityRetryUsed: progress.identityRetryUsed,
    loggedIn: progress.loggedIn,
    idleDeadlineAt: progress.idleDeadlineAt,
    absoluteDeadlineAt: progress.absoluteDeadlineAt,
    tabId,
    ...(pendingResult ? { pendingResult } : {}),
  };
}

function serializedStorageMutation(mutation: () => Promise<void>): Promise<void> {
  const current = storageMutation.then(mutation, mutation);
  storageMutation = current.catch(() => {});
  return current;
}

async function persistState(): Promise<void> {
  const state = serializeState();
  const storage = storageArea();
  if (!state || !storage) return;
  await serializedStorageMutation(async () => {
    await storage.set({ [SESSION_KEY]: state });
  });
}

async function clearPersistedState(): Promise<void> {
  const storage = storageArea();
  if (!storage) return;
  await serializedStorageMutation(async () => {
    await storage.remove(SESSION_KEY);
  }).catch(() => {});
}

async function loadPersistedState(): Promise<PersistedState | null> {
  const storage = storageArea();
  if (!storage) return null;
  try {
    const stored = await storage.get(SESSION_KEY);
    const value = stored[SESSION_KEY];
    if (!value || typeof value !== "object") return null;
    const state = value as Partial<PersistedState>;
    if (
      state.version !== 1
      || !isValidV2EXTask(state.task)
      || !Array.isArray(state.scopes)
      || state.scopes.some((scope) => !DEFAULT_SCOPES.includes(scope))
      || typeof state.scopeIndex !== "number"
      || typeof state.page !== "number"
      || typeof state.maxPages !== "number"
      || typeof state.idleDeadlineAt !== "number"
      || typeof state.absoluteDeadlineAt !== "number"
    ) {
      await removePersistedTaskTabIfOwned(state.tabId);
      return null;
    }
    return state as PersistedState;
  } catch {
    return null;
  }
}

function hydrateProgress(state: PersistedState): Progress {
  const items = new Map<string, V2EXBootstrapItem>();
  for (const item of Array.isArray(state.items) ? state.items : []) {
    const key = itemKey(item);
    if (key) items.set(key, item);
  }
  return {
    task: state.task,
    scopes: [...state.scopes],
    scopeIndex: Math.max(0, Math.floor(state.scopeIndex)),
    page: Math.max(1, Math.floor(state.page)),
    maxPages: Math.max(1, Math.floor(state.maxPages)),
    username: String(state.username || "").trim(),
    observedUsername: String(state.observedUsername || "").trim(),
    items,
    scopeCounts: { ...(state.scopeCounts || {}) },
    scopeComplete: { ...(state.scopeComplete || {}) },
    scopeStatuses: { ...(state.scopeStatuses || {}) },
    failures: Array.isArray(state.failures) ? state.failures.map(String) : [],
    identityRetryUsed: state.identityRetryUsed === true,
    loggedIn: state.loggedIn === true,
    idleDeadlineAt: state.idleDeadlineAt,
    absoluteDeadlineAt: state.absoluteDeadlineAt,
  };
}

async function fetchNextTask(): Promise<V2EXTask | null> {
  try {
    const response = await authenticatedFetch(await apiUrl("/sources/v2ex/next-task"));
    if (response.status === 204 || !response.ok) return null;
    const value: unknown = await response.json();
    return isValidV2EXTask(value) ? value : null;
  } catch {
    return null;
  }
}

async function removeTabBestEffort(id: number): Promise<void> {
  try {
    await chrome.tabs.remove(id);
  } catch {
    // The user may have closed the task tab already.
  }
}

function isOwnedTaskTabUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:"
      && (url.hostname === "v2ex.com" || url.hostname.endsWith(".v2ex.com"))
      && isV2EXTaskTabLocation(url)
    );
  } catch {
    return false;
  }
}

async function removePersistedTaskTabIfOwned(value: unknown): Promise<void> {
  const id = Number(value);
  if (!Number.isInteger(id)) return;
  try {
    const tab = await chrome.tabs.get(id);
    if (isOwnedTaskTabUrl(String(tab.url || ""))) await removeTabBestEffort(id);
  } catch {
    // Already gone, or no longer visible to this extension instance.
  }
}

async function cleanupAfterAck(): Promise<void> {
  if (deadlineTimer !== null) clearTimeout(deadlineTimer);
  deadlineTimer = null;
  const ownedTabId = tabId;
  tabId = null;
  progress = null;
  pendingResult = null;
  inFlight = false;
  navigationGeneration += 1;
  try {
    await chrome.alarms.clear(RESULT_RETRY_ALARM_NAME);
  } catch {
    // Alarm cleanup is best-effort after the authoritative ACK.
  }
  await clearPersistedState();
  if (ownedTabId !== null) await removeTabBestEffort(ownedTabId);
  releaseMutex();
}

function schedulePendingResultRetry(): void {
  try {
    chrome.alarms.create(RESULT_RETRY_ALARM_NAME, {
      delayInMinutes: RESULT_RETRY_DELAY_MINUTES,
    });
  } catch {
    // The durable session row is still retried on the next startup/poll wake.
  }
}

async function finalizeWithResult(result: V2EXTaskResultPayload): Promise<boolean> {
  pendingResult = result;
  if (deadlineTimer !== null) clearTimeout(deadlineTimer);
  deadlineTimer = null;
  try {
    // Persist the exact payload before its first network attempt. A restarted
    // worker can therefore only replay these bytes, never rebuild a different
    // terminal result from a half-restored in-memory accumulator.
    await persistState();
  } catch {
    // The backend lease still provides recovery if session storage itself is unavailable.
  }
  try {
    await postV2EXTaskResult(result);
  } catch {
    schedulePendingResultRetry();
    return false;
  }
  await cleanupAfterAck();
  return true;
}

async function retryPendingResult(): Promise<void> {
  if (!pendingResult) return;
  try {
    await postV2EXTaskResult(pendingResult);
  } catch {
    schedulePendingResultRetry();
    return;
  }
  await cleanupAfterAck();
}

function timeoutResult(error: "task_idle_timeout" | "task_absolute_timeout"): V2EXTaskResultPayload | null {
  if (!progress) return null;
  const items = [...progress.items.values()];
  return {
    task_id: progress.task.id,
    status: items.length ? "partial" : "failed",
    items,
    scope_counts: { ...progress.scopeCounts },
    error,
    debug: {
      username: progress.observedUsername,
      logged_in: progress.loggedIn,
      failures: [...progress.failures, error],
      scope_complete: { ...progress.scopeComplete },
      scope_statuses: { ...progress.scopeStatuses },
    },
  };
}

async function handleDeadline(): Promise<void> {
  if (!progress) return;
  if (pendingResult) {
    await retryPendingResult();
    return;
  }
  const now = Date.now();
  if (now < Math.min(progress.idleDeadlineAt, progress.absoluteDeadlineAt)) {
    armDeadlineTimer();
    return;
  }
  const error = now >= progress.absoluteDeadlineAt
    ? "task_absolute_timeout"
    : "task_idle_timeout";
  const result = timeoutResult(error);
  if (result) await finalizeWithResult(result);
}

function armDeadlineTimer(): void {
  if (deadlineTimer !== null) clearTimeout(deadlineTimer);
  deadlineTimer = null;
  if (!progress || pendingResult) return;
  const deadline = Math.min(progress.idleDeadlineAt, progress.absoluteDeadlineAt);
  deadlineTimer = setTimeout(() => {
    deadlineTimer = null;
    void handleDeadline();
  }, Math.max(1, deadline - Date.now()));
}

function recordAcceptedProgress(): void {
  if (!progress) return;
  progress.idleDeadlineAt = Math.min(
    Date.now() + IDLE_TIMEOUT_MS,
    progress.absoluteDeadlineAt,
  );
  refreshMutex();
  armDeadlineTimer();
}

function waitForTab(tab: number, generation: number, callback: () => void): void {
  let finished = false;
  const run = (): void => {
    if (finished || generation !== navigationGeneration) return;
    finished = true;
    chrome.tabs.onUpdated.removeListener(listener);
    callback();
  };
  const listener = (updatedId: number, info: { status?: string }): void => {
    if (updatedId === tab && info.status === "complete") run();
  };
  chrome.tabs.onUpdated.addListener(listener);
  setTimeout(run, 12_000);
  void chrome.tabs.get(tab).then((current) => {
    if (current.status === "complete") run();
  }).catch(run);
}

function sendScope(): void {
  if (!progress || tabId === null || pendingResult) return;
  const scope = progress.scopes[progress.scopeIndex];
  if (!scope) return;
  const limit = scopeItemLimit(progress.task, scope);
  const remaining = Math.max(0, limit - (progress.scopeCounts[scope] || 0));
  void chrome.tabs.sendMessage(tabId, {
    action: "V2EX_SCOPE_EXECUTE",
    data: {
      task_id: progress.task.id,
      scope,
      username: progress.username,
      page: progress.page,
      // One proof row after reaching a cap distinguishes complete-empty from
      // truncated-with-more-data without allowing the browser to expand caps.
      max_items: Math.max(1, remaining),
    },
  }).catch(() => {
    if (!progress) return;
    void applyV2EXScopeResult({
      task_id: progress.task.id,
      scope,
      status: "failed",
      items: [],
      scope_count: 0,
      error: "sendMessage_failed",
    }, undefined, true);
  });
}

async function navigate(): Promise<void> {
  if (!progress || tabId === null || pendingResult) return;
  const scope = progress.scopes[progress.scopeIndex];
  if (!scope) return;
  const generation = ++navigationGeneration;
  try {
    await chrome.tabs.update(tabId, {
      active: false,
      url: v2exScopeUrl(scope, progress.username, progress.page),
    });
    waitForTab(tabId, generation, sendScope);
  } catch {
    await applyV2EXScopeResult({
      task_id: progress.task.id,
      scope,
      status: "failed",
      items: [],
      scope_count: 0,
      error: "navigation_failed",
    }, undefined, true);
  }
}

function itemKey(item: V2EXBootstrapItem): string {
  if (item.scope === "favorite_nodes") {
    return `${item.scope}:node:${item.node_name || item.title || item.url || ""}`;
  }
  return `${item.scope}:topic:${item.topic_id || item.url || item.title || ""}`;
}

function boundedLimit(value: number | undefined, fallback: number): number {
  return Math.max(1, Math.min(MAX_WIRE_LIMIT, Math.floor(Number(value) || fallback)));
}

function scopeItemLimit(task: V2EXTask, scope: V2EXScope): number {
  if (scope === "public_topics") return boundedLimit(task.max_topics, 100);
  if (scope === "public_replies") return boundedLimit(task.max_replies, 300);
  if (scope === "favorite_topics") return boundedLimit(task.max_favorite_topics, 300);
  return MAX_WIRE_LIMIT;
}

export type V2EXTaskExecutionDisposition = "accepted" | "declined";

export async function executeV2EXTask(
  task: V2EXTask,
  mutexAlreadyHeld = false,
): Promise<V2EXTaskExecutionDisposition> {
  if (inFlight) return "declined";
  if (!mutexAlreadyHeld && !tryAcquireMutex()) return "declined";
  inFlight = true;
  const now = Date.now();
  const scopes = task.scopes?.length ? [...task.scopes] : [...DEFAULT_SCOPES];
  progress = {
    task,
    scopes,
    scopeIndex: 0,
    page: 1,
    maxPages: boundedLimit(task.max_pages_per_scope, 20),
    username: String(task.username || "").trim(),
    observedUsername: "",
    items: new Map(),
    scopeCounts: {},
    scopeComplete: {},
    scopeStatuses: {},
    failures: [],
    identityRetryUsed: false,
    loggedIn: false,
    idleDeadlineAt: now + IDLE_TIMEOUT_MS,
    absoluteDeadlineAt: now + ABSOLUTE_TIMEOUT_MS,
  };
  pendingResult = null;
  tabId = null;
  try {
    await persistState();
    const tab = await createTaskTab({
      url: v2exScopeUrl(scopes[0], progress.username, 1),
      active: false,
    });
    tabId = tab.id ?? null;
    await persistState();
  } catch {
    const failed: V2EXTaskResultPayload = {
      task_id: task.id,
      status: "failed",
      items: [],
      scope_counts: {},
      error: "tab_create_failed",
    };
    await finalizeWithResult(failed);
    return "accepted";
  }
  if (tabId === null) {
    await finalizeWithResult({
      task_id: task.id,
      status: "failed",
      items: [],
      scope_counts: {},
      error: "tab_id_unknown",
    });
    return "accepted";
  }
  armDeadlineTimer();
  const generation = ++navigationGeneration;
  waitForTab(tabId, generation, sendScope);
  return "accepted";
}

function resultCameFromTaskTab(
  sender?: chrome.runtime.MessageSender,
  trustedInternal = false,
): boolean {
  if (trustedInternal) return true;
  return v2exTaskMessageSenderMatches(tabId, sender);
}

export function v2exTaskMessageSenderMatches(
  expectedTabId: number | null,
  sender?: chrome.runtime.MessageSender,
): boolean {
  if (sender?.tab?.id === undefined || expectedTabId === null) return false;
  if (sender.tab.id !== expectedTabId) return false;
  const senderUrl = sender.url || sender.tab.url || "";
  return isOwnedTaskTabUrl(senderUrl);
}

async function applyV2EXScopeResult(
  result: V2EXScopeResult,
  sender?: chrome.runtime.MessageSender,
  trustedInternal = false,
): Promise<void> {
  if (!progress || pendingResult || result.task_id !== progress.task.id) return;
  if (!resultCameFromTaskTab(sender, trustedInternal)) return;
  const scope = progress.scopes[progress.scopeIndex];
  if (result.scope !== scope) return;
  const acceptedPage = result.status === "ok" || result.status === "empty";
  const observedUsername = typeof result.debug?.username === "string"
    ? result.debug.username.trim()
    : "";
  if (observedUsername) {
    progress.observedUsername = observedUsername;
    if (!progress.username) progress.username = observedUsername;
  }
  if (typeof result.debug?.logged_in === "boolean") progress.loggedIn = result.debug.logged_in;
  progress.scopeStatuses[scope] = result.status;
  if (!acceptedPage) progress.failures.push(`${scope}:${result.error || result.status}`);

  const pageUrl = typeof result.debug?.page_url === "string" ? result.debug.page_url : "";
  let identityLanding = false;
  try {
    identityLanding = new URL(pageUrl).pathname === "/";
  } catch {
    identityLanding = false;
  }
  const needsIdentityLookup =
    (scope === "public_topics" || scope === "public_replies")
    && !progress.task.username
    && !progress.identityRetryUsed
    && identityLanding;
  if (needsIdentityLookup && observedUsername && acceptedPage) {
    progress.identityRetryUsed = true;
    progress.page = 1;
    recordAcceptedProgress();
    await persistState();
    await navigate();
    return;
  }

  const limit = scopeItemLimit(progress.task, scope);
  const countBeforePage = progress.scopeCounts[scope] || 0;
  if (needsIdentityLookup) {
    if (acceptedPage) progress.failures.push(`${scope}:identity_unknown`);
    progress.scopeCounts[scope] = 0;
    progress.scopeComplete[scope] = false;
  } else if (acceptedPage) {
    let accepted = countBeforePage;
    for (const item of result.items || []) {
      if (accepted >= limit) break;
      const key = itemKey(item);
      if (key && !progress.items.has(key)) {
        progress.items.set(key, item);
        accepted += 1;
      }
    }
    progress.scopeCounts[scope] = accepted;
  }

  const pageTruncated = result.debug?.page_truncated === true
    || (countBeforePage >= limit && result.items.length > 0);
  const decision = needsIdentityLookup
    ? { continuePaging: false, complete: false, truncated: false }
    : v2exScopePageDecision(
      scope,
      progress.page,
      progress.maxPages,
      result.status,
      result.items.length,
      pageTruncated,
      typeof result.debug?.has_next_page === "boolean"
        ? result.debug.has_next_page
        : undefined,
    );
  progress.scopeComplete[scope] = decision.complete;
  if (decision.truncated) {
    progress.failures.push(`${scope}:${pageTruncated ? "item_limit_reached" : "max_pages_reached"}`);
  }
  if (acceptedPage) recordAcceptedProgress();
  if (decision.continuePaging) {
    progress.page += 1;
    await persistState();
    await navigate();
    return;
  }
  progress.scopeIndex += 1;
  progress.page = 1;
  if (progress.scopeIndex < progress.scopes.length) {
    await persistState();
    await navigate();
    return;
  }

  const items = [...progress.items.values()];
  await finalizeWithResult({
    task_id: progress.task.id,
    status: progress.failures.length ? "partial" : (items.length ? "ok" : "empty"),
    items,
    scope_counts: { ...progress.scopeCounts },
    debug: {
      username: progress.observedUsername,
      logged_in: progress.loggedIn,
      failures: [...progress.failures],
      scope_complete: { ...progress.scopeComplete },
      scope_statuses: { ...progress.scopeStatuses },
    },
  });
}

export function handleV2EXScopeResult(
  result: V2EXScopeResult,
  sender?: chrome.runtime.MessageSender,
): Promise<void> {
  const running = resultMutation.then(
    () => applyV2EXScopeResult(result, sender),
    () => applyV2EXScopeResult(result, sender),
  );
  resultMutation = running.catch(() => {});
  return running;
}

async function recoverPersistedTask(): Promise<void> {
  const state = await loadPersistedState();
  if (!state) {
    await clearPersistedState();
    return;
  }
  if (!tryAcquireMutex()) {
    schedulePendingResultRetry();
    return;
  }
  inFlight = true;
  progress = hydrateProgress(state);
  pendingResult = state.pendingResult || null;
  tabId = Number.isInteger(state.tabId) ? state.tabId : null;
  if (pendingResult) {
    await retryPendingResult();
    return;
  }
  if (Date.now() >= Math.min(progress.idleDeadlineAt, progress.absoluteDeadlineAt)) {
    await handleDeadline();
    return;
  }
  if (tabId !== null) {
    try {
      await chrome.tabs.get(tabId);
    } catch {
      tabId = null;
    }
  }
  if (tabId === null) {
    try {
      const scope = progress.scopes[progress.scopeIndex];
      const tab = await createTaskTab({
        active: false,
        url: v2exScopeUrl(scope, progress.username, progress.page),
      });
      tabId = tab.id ?? null;
      await persistState();
    } catch {
      const failed = timeoutResult("task_idle_timeout");
      if (failed) {
        failed.error = "recovery_tab_create_failed";
        await finalizeWithResult(failed);
      }
      return;
    }
  }
  armDeadlineTimer();
  await navigate();
}

/** MV3-lifetime recovery barrier; callers must await it before claiming. */
export function ensureV2EXTaskRecovery(): Promise<void> {
  recoveryPromise ??= recoverPersistedTask();
  return recoveryPromise;
}

export function resetV2EXTaskRecoveryForTest(): void {
  recoveryPromise = null;
  storageMutation = Promise.resolve();
  resultMutation = Promise.resolve();
  pollInFlight = null;
}

export interface V2EXTaskPollDependencies {
  ensureRecovery: () => Promise<void>;
  canExecute?: () => boolean;
  fetchTask: () => Promise<V2EXTask | null>;
  execute: (
    task: V2EXTask,
    mutexAlreadyHeld: boolean,
  ) => Promise<V2EXTaskExecutionDisposition>;
  reportDeclined: (task: V2EXTask) => Promise<void>;
}

async function reportDeclinedTask(task: V2EXTask): Promise<void> {
  await postV2EXTaskResult({
    task_id: task.id,
    status: "failed",
    items: [],
    scope_counts: {},
    error: "dispatcher_busy_after_claim",
  });
}

const POLL_DEPENDENCIES: V2EXTaskPollDependencies = {
  ensureRecovery: ensureV2EXTaskRecovery,
  canExecute: () => {
    try {
      return (
        typeof globalThis.chrome?.tabs?.create === "function"
        && storageArea() !== null
      );
    } catch {
      return false;
    }
  },
  fetchTask: fetchNextTask,
  execute: executeV2EXTask,
  reportDeclined: reportDeclinedTask,
};

export function pollV2EXTaskOnce(
  dependencies: V2EXTaskPollDependencies = POLL_DEPENDENCIES,
): Promise<void> {
  if (pollInFlight) return pollInFlight;
  const running = (async () => {
    await dependencies.ensureRecovery();
    if (inFlight) return;
    if (dependencies.canExecute && !dependencies.canExecute()) return;
    // GET /next-task is a write-shaped atomic claim. Hold the cross-source
    // execution slot before calling it so a busy sibling can never strand the
    // newly claimed V2EX row.
    if (!tryAcquireMutex()) return;
    let releaseOnExit = true;
    try {
      const task = await dependencies.fetchTask();
      if (!task) return;
      const disposition = await dependencies.execute(task, true);
      if (disposition === "declined") {
        await dependencies.reportDeclined(task);
        return;
      }
      releaseOnExit = false;
    } finally {
      if (releaseOnExit) releaseMutex();
    }
  })();
  pollInFlight = running;
  const clear = (): void => {
    if (pollInFlight === running) pollInFlight = null;
  };
  void running.then(clear, clear);
  return running;
}

export function startV2EXTaskPolling(): void {
  if (typeof chrome === "undefined" || !chrome.alarms) return;
  chrome.alarms.create(POLL_ALARM_NAME, {
    periodInMinutes: POLL_INTERVAL_MS / 60_000,
  });
}

export function handleV2EXTaskAlarm(alarmName: string): void {
  if (alarmName === RESULT_RETRY_ALARM_NAME) {
    void retryPendingResult();
    return;
  }
  if (alarmName === POLL_ALARM_NAME) void pollV2EXTaskOnce();
}

export function pollV2EXTaskNow(): void {
  void pollV2EXTaskOnce();
}
