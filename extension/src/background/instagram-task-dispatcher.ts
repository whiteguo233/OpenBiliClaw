/** Durable dispatcher for bounded, read-only Instagram browser tasks. */

import {
  INSTAGRAM_API_REQUEST_TIMEOUT_MS,
  isKnownInstagramScope,
  type InstagramTask,
  type InstagramTaskProgress,
  type InstagramTaskResult,
  type InstagramWireItem,
} from "../content/instagram/task-executor.ts";
import {
  isInstagramTaskTabLocation,
  withInstagramTaskMarker,
} from "../content/instagram/task-mode.ts";
import { isInstagramHost } from "../shared/platforms/instagram.ts";
import { apiUrl } from "../shared/backend-endpoint.ts";
import { authenticatedFetch } from "../shared/auth.ts";
import {
  releaseDispatcherMutex,
  tryAcquireDispatcherMutex,
} from "./dispatcher-mutex.ts";

const OWNER = "instagram";
const POLL_ALARM = "openbiliclaw-instagram-task-poll";
const POLL_RETRY_ALARM = "openbiliclaw-instagram-poll-retry";
const RESULT_RETRY_ALARM = "openbiliclaw-instagram-result-retry";
const SESSION_KEY = "openbiliclaw_instagram_task_state_v1";
const POLL_INTERVAL_MINUTES = 1;
const RESULT_RETRY_MINUTES = 0.25;
const IDLE_TIMEOUT_MS = 90_000;
const ABSOLUTE_TIMEOUT_MS = 12 * 60_000;
const RESULT_ATTEMPTS = 3;
const RESULT_BACKOFF_MS = 250;
const RESULT_BACKOFF_MAX_MS = 2_000;
const MAX_ITEMS = 10_000;

interface PersistedState {
  version: 1;
  task: InstagramTask;
  tabId: number | null;
  lastProgress: InstagramTaskProgress | null;
  idleDeadlineAt: number;
  absoluteDeadlineAt: number;
  dispatchAttempts: number;
  reloadUsed: boolean;
  acceptedItems: InstagramWireItem[];
  scopeCounts: Record<string, number>;
  scopeComplete: Record<string, boolean>;
  accountId?: string;
  progressDebug?: Record<string, unknown>;
  pendingResult?: InstagramTaskResult;
  pendingResultBody?: string;
}

export interface InstagramResultTransport {
  resolveUrl: (path: string) => Promise<string>;
  fetch: (input: string, init: RequestInit) => Promise<{ ok: boolean; status: number }>;
  sleep: (milliseconds: number) => Promise<void>;
}

class InstagramTerminalResultRejection extends Error {}

const sleep = (milliseconds: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));
const RESULT_TRANSPORT: InstagramResultTransport = {
  resolveUrl: apiUrl,
  fetch: authenticatedFetch,
  sleep,
};

let inFlight = false;
let pollPromise: Promise<void> | null = null;
let recoveryPromise: Promise<void> | null = null;
let state: PersistedState | null = null;
let deadlineTimer: ReturnType<typeof setTimeout> | null = null;
let storageMutation: Promise<void> = Promise.resolve();
let resultMutation: Promise<void> = Promise.resolve();
let navigationGeneration = 0;
let ownsMutex = false;
let initialPersistencePending = false;

function string(value: unknown, maximum = 1024): string {
  return typeof value === "string" ? value.trim().slice(0, maximum) : "";
}

function positiveInteger(value: unknown, maximum = MAX_ITEMS): number | null {
  const parsed = Math.floor(Number(value));
  return Number.isFinite(parsed) && parsed > 0 ? Math.min(maximum, parsed) : null;
}

function idleTimeoutForTask(task: InstagramTask): number {
  if (task.type !== "bootstrap_events") return IDLE_TIMEOUT_MS;
  const interval = positiveInteger(task.request_interval_ms, 30_000) || 3_000;
  // One private page needs before-viewer, page GET and after-viewer requests;
  // allow the scope transition wait plus bounded response/ACK time as well.
  // Only accepted durable progress renews this window; absolute stays 12 min.
  return Math.max(IDLE_TIMEOUT_MS, 4 * interval + 3 * INSTAGRAM_API_REQUEST_TIMEOUT_MS + 5_000);
}

/** Reject malformed/expanded task claims before opening an upstream tab. */
export function isValidInstagramTask(value: unknown): value is InstagramTask {
  if (!value || typeof value !== "object") return false;
  const task = value as Record<string, unknown>;
  if (!string(task.id, 256) || !string(task.claim_token, 2048)) return false;
  if (task.type === "discover") {
    if (task.mode !== "topic" && task.mode !== "creator") return false;
    if (task.mode === "topic" && !string(task.query, 300)) return false;
    if (task.mode === "creator" && !string(task.username, 128)) return false;
    if (
      !Number.isInteger(task.max_items)
      || Number(task.max_items) <= 0
      || Number(task.max_items) > 300
      || !Number.isInteger(task.max_pages)
      || Number(task.max_pages) <= 0
      || Number(task.max_pages) > 20
    ) return false;
    if (task.cursor !== undefined && typeof task.cursor !== "string") return false;
    if (
      task.source_keyword_id !== undefined
      && (!Number.isInteger(task.source_keyword_id) || Number(task.source_keyword_id) <= 0)
    ) return false;
    if (
      task.request_interval_ms !== undefined
      && (!Number.isInteger(task.request_interval_ms)
        || Number(task.request_interval_ms) < 1_000
        || Number(task.request_interval_ms) > 30_000)
    ) return false;
    return true;
  }
  if (task.type !== "bootstrap_events") return false;
  if (
    !Array.isArray(task.scopes)
    || task.scopes.length === 0
    || task.scopes.some((scope) => !isKnownInstagramScope(scope))
    || new Set(task.scopes).size !== task.scopes.length
  ) return false;
  if (
    task.request_interval_ms !== undefined
    && (!Number.isInteger(task.request_interval_ms)
      || Number(task.request_interval_ms) < 1_000
      || Number(task.request_interval_ms) > 30_000)
  ) return false;
  return Boolean(
    Number.isInteger(task.max_items_per_scope)
    && Number(task.max_items_per_scope) > 0
    && Number(task.max_items_per_scope) <= 300
    && Number.isInteger(task.max_pages_per_scope)
    && Number(task.max_pages_per_scope) > 0
    && Number(task.max_pages_per_scope) <= 100,
  );
}

export function instagramTaskUrl(task: InstagramTask): string {
  if (task.type === "bootstrap_events") {
    return withInstagramTaskMarker("https://www.instagram.com/your_activity/interactions/likes/");
  }
  if (task.mode === "creator") {
    return withInstagramTaskMarker(
      `https://www.instagram.com/${encodeURIComponent(string(task.username, 128))}/`,
    );
  }
  const topic = string(task.query, 300).replace(/^#+/, "").replace(/\s+/g, "-");
  return withInstagramTaskMarker(`https://www.instagram.com/popular/${encodeURIComponent(topic)}/`);
}

function ownedTaskUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:"
      && isInstagramHost(url.hostname)
      && isInstagramTaskTabLocation(url);
  } catch {
    return false;
  }
}

export function instagramTaskSenderMatches(
  expectedTabId: number | null,
  sender?: chrome.runtime.MessageSender,
): boolean {
  if (expectedTabId === null || sender?.tab?.id === undefined) return false;
  if (sender.tab.id !== expectedTabId) return false;
  return ownedTaskUrl(sender.url || sender.tab.url || "");
}

function storageArea(): chrome.storage.StorageArea | null {
  try {
    // Session survives MV3 worker recycling, but not extension/browser reload.
    // Keep the bounded claim/outbox until ACK; never store Instagram credentials.
    return typeof chrome === "undefined" ? null : chrome.storage?.local ?? null;
  } catch {
    return null;
  }
}

function serializedStorageMutation(mutation: () => Promise<void>): Promise<void> {
  const current = storageMutation.then(mutation, mutation);
  storageMutation = current.catch(() => {});
  return current;
}

async function persistState(): Promise<void> {
  const storage = storageArea();
  if (!storage || !state) throw new Error("instagram_task_state_unavailable");
  await serializedStorageMutation(async () => storage.set({ [SESSION_KEY]: state }));
}

async function clearPersistedState(): Promise<void> {
  const storage = storageArea();
  if (!storage) return;
  await serializedStorageMutation(async () => {
    // Retire legacy session data too, so it cannot resurrect an ACKed claim.
    await chrome.storage?.session?.remove(SESSION_KEY);
    await storage.remove(SESSION_KEY);
  }).catch(() => {});
}

async function loadPersistedState(): Promise<PersistedState | null> {
  const storage = storageArea();
  if (!storage) throw new Error("instagram_task_state_unavailable");
  let stored = await storage.get(SESSION_KEY);
  if (stored[SESSION_KEY] === undefined && chrome.storage?.session) {
    // One-way migration of an already-running task from the previous build.
    stored = await chrome.storage.session.get(SESSION_KEY);
    if (stored[SESSION_KEY] !== undefined) {
      await serializedStorageMutation(async () => storage.set(stored));
    }
  }
  const value = stored[SESSION_KEY] as Partial<PersistedState> | undefined;
  if (value === undefined) return null;
  if (
    value.version !== 1
    || !isValidInstagramTask(value.task)
    || typeof value.idleDeadlineAt !== "number"
    || typeof value.absoluteDeadlineAt !== "number"
    || (value.tabId !== null && !Number.isInteger(value.tabId))
    || typeof value.dispatchAttempts !== "number"
    || typeof value.reloadUsed !== "boolean"
  ) throw new Error("instagram_persisted_state_invalid");
  const task = value.task;
  const acceptedItems = Array.isArray(value.acceptedItems)
    ? itemsForTask(value.acceptedItems, task)
    : [];
  return {
    version: 1,
    task,
    tabId: value.tabId ?? null,
    lastProgress: value.lastProgress || null,
    idleDeadlineAt: value.idleDeadlineAt,
    absoluteDeadlineAt: value.absoluteDeadlineAt,
    dispatchAttempts: value.dispatchAttempts,
    reloadUsed: value.reloadUsed,
    acceptedItems,
    scopeCounts: countsForItems(task, acceptedItems),
    scopeComplete: normalizedScopeComplete(task, value.scopeComplete),
    ...(/^\d+$/.test(string(value.accountId, 128))
      ? { accountId: string(value.accountId, 128) }
      : {}),
    ...(safeDebug(value.progressDebug) ? { progressDebug: safeDebug(value.progressDebug) } : {}),
    ...(value.pendingResult ? { pendingResult: value.pendingResult } : {}),
    ...(string(value.pendingResultBody, 2_000_000)
      ? { pendingResultBody: string(value.pendingResultBody, 2_000_000) }
      : {}),
  };
}

async function fetchNextTask(): Promise<InstagramTask | null> {
  try {
    if (chrome.permissions?.contains) {
      const granted = await chrome.permissions.contains({
        origins: ["https://*.instagram.com/*"],
      });
      if (!granted) return null;
    }
  } catch {
    return null;
  }
  try {
    const response = await authenticatedFetch(await apiUrl("/sources/instagram/next-task"));
    if (response.status === 204 || !response.ok) return null;
    const payload: unknown = await response.json();
    return isValidInstagramTask(payload) ? payload : null;
  } catch {
    return null;
  }
}

function safeDebug(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const output: Record<string, unknown> = {};
  for (const [key, entry] of Object.entries(value as Record<string, unknown>).slice(0, 30)) {
    if (/cookie|authorization|header|raw|body|csrf|session/i.test(key)) continue;
    if (typeof entry === "boolean" || typeof entry === "number") output[key] = entry;
    else if (typeof entry === "string") output[key] = entry.slice(0, 1024);
    else if (Array.isArray(entry)) output[key] = entry.slice(0, 50).map((item) => string(item, 256));
  }
  return output;
}

function safeItem(value: unknown): InstagramWireItem | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Partial<InstagramWireItem>;
  const id = string(item.id, 128);
  const url = string(item.url, 4096);
  if (!id || !url || !["post", "reel", "carousel", "user"].includes(String(item.content_type))) {
    return null;
  }
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || !isInstagramHost(parsed.hostname)) return null;
  } catch {
    return null;
  }
  const scope = item.scope && isKnownInstagramScope(item.scope) ? item.scope : undefined;
  const coverUrl = string(item.cover_url, 4096);
  let safeCoverUrl = "";
  if (coverUrl) {
    try {
      const parsed = new URL(coverUrl);
      const host = parsed.hostname.toLowerCase();
      if (
        parsed.protocol === "https:"
        && (isInstagramHost(host) || host.endsWith(".cdninstagram.com") || host.endsWith(".fbcdn.net"))
      ) safeCoverUrl = parsed.href;
    } catch {
      // Untrusted bridge metadata is omitted rather than forwarded.
    }
  }
  return {
    ...(scope ? { scope } : {}),
    id,
    ...(string(item.code, 128) ? { code: string(item.code, 128) } : {}),
    content_type: item.content_type as InstagramWireItem["content_type"],
    url,
    ...(string(item.title, 300) ? { title: string(item.title, 300) } : {}),
    ...(string(item.description, 6000) ? { description: string(item.description, 6000) } : {}),
    ...(safeCoverUrl ? { cover_url: safeCoverUrl } : {}),
    ...(string(item.author_id, 128) ? { author_id: string(item.author_id, 128) } : {}),
    ...(string(item.author_name, 128) ? { author_name: string(item.author_name, 128) } : {}),
    ...(string(item.published_at, 64) ? { published_at: string(item.published_at, 64) } : {}),
  };
}

function taskScopes(task: InstagramTask): string[] {
  return task.type === "discover" ? ["discover"] : [...task.scopes];
}

function resultItemCap(task: InstagramTask): number {
  return task.type === "discover"
    ? Math.min(300, task.max_items)
    : Math.min(MAX_ITEMS, task.scopes.length * Math.min(300, task.max_items_per_scope));
}

function itemsForTask(values: readonly unknown[], task: InstagramTask): InstagramWireItem[] {
  const output = new Map<string, InstagramWireItem>();
  for (const value of values) {
    const item = safeItem(value);
    if (!item) continue;
    if (task.type === "discover" && item.scope !== undefined) continue;
    if (task.type === "bootstrap_events" && (!item.scope || !task.scopes.includes(item.scope))) {
      continue;
    }
    const key = `${item.scope || "discover"}\u0000${item.content_type}\u0000${item.id}`;
    if (!output.has(key)) output.set(key, item);
    if (output.size >= resultItemCap(task)) break;
  }
  return [...output.values()];
}

function countsForItems(task: InstagramTask, items: readonly InstagramWireItem[]): Record<string, number> {
  const counts = Object.fromEntries(taskScopes(task).map((scope) => [scope, 0]));
  for (const item of items) {
    const scope = task.type === "discover" ? "discover" : item.scope;
    if (scope && scope in counts) counts[scope] += 1;
  }
  return counts;
}

function normalizedScopeComplete(
  task: InstagramTask,
  value: unknown,
): Record<string, boolean> {
  const candidate = value && typeof value === "object"
    ? value as Record<string, unknown>
    : {};
  return Object.fromEntries(taskScopes(task).map((scope) => [scope, candidate[scope] === true]));
}

export function sanitizeInstagramTaskResult(
  result: InstagramTaskResult,
  task: InstagramTask,
): InstagramTaskResult | null {
  if (result.task_id !== task.id || result.claim_token !== task.claim_token) return null;
  if (!["ok", "empty", "partial", "failed"].includes(result.status)) return null;
  if (!Array.isArray(result.items)) return null;
  const items = itemsForTask(result.items, task);
  if (
    task.type === "bootstrap_events"
    && result.status !== "failed"
    && !/^\d+$/.test(string(result.account_id, 128))
  ) {
    return {
      task_id: task.id,
      claim_token: task.claim_token,
      status: "failed",
      items: [],
      scope_counts: {},
      scope_complete: Object.fromEntries(task.scopes.map((scope) => [scope, false])),
      error: "account_identity_missing",
      debug: { identity_resolved: false, response_observed: false },
    };
  }
  const expectedScopes = taskScopes(task);
  const counts: Record<string, number> = Object.fromEntries(
    expectedScopes.map((scope) => [scope, 0]),
  );
  const complete: Record<string, boolean> = Object.fromEntries(
    expectedScopes.map((scope) => [scope, false]),
  );
  for (const [key, value] of Object.entries(result.scope_counts || {}).slice(0, 20)) {
    if (!expectedScopes.includes(key as never)) continue;
    const count = Math.max(0, Math.min(MAX_ITEMS, Math.floor(Number(value) || 0)));
    counts[string(key, 128)] = count;
  }
  for (const [key, value] of Object.entries(result.scope_complete || {}).slice(0, 20)) {
    if (!expectedScopes.includes(key as never)) continue;
    complete[string(key, 128)] = value === true;
  }
  const debug = safeDebug(result.debug);
  if (
    result.status !== "failed"
    && (debug?.response_observed !== true || !string(debug?.terminal_evidence, 1024))
  ) {
    return {
      task_id: task.id,
      claim_token: task.claim_token,
      status: "failed",
      items: [],
      scope_counts: Object.fromEntries(expectedScopes.map((scope) => [scope, 0])),
      scope_complete: Object.fromEntries(expectedScopes.map((scope) => [scope, false])),
      error: "terminal_evidence_missing",
      debug: { response_observed: false },
    };
  }
  return {
    task_id: task.id,
    claim_token: task.claim_token,
    status: result.status,
    items,
    scope_counts: counts,
    scope_complete: complete,
    ...(task.type === "bootstrap_events" && /^\d+$/.test(string(result.account_id, 128))
      ? { account_id: string(result.account_id, 128) }
      : {}),
    ...(string(result.error, 1024) ? { error: string(result.error, 1024) } : {}),
    ...(debug ? { debug } : {}),
  };
}

/** Retry the exact serialized callback bytes until a 2xx ACK. */
export async function postInstagramTaskResult(
  result: InstagramTaskResult,
  transport: InstagramResultTransport = RESULT_TRANSPORT,
  attempts = RESULT_ATTEMPTS,
  serializedBody = JSON.stringify(result),
): Promise<void> {
  const body = serializedBody;
  let failure = "unacknowledged";
  for (let attempt = 1; attempt <= Math.max(1, Math.min(5, attempts)); attempt += 1) {
    try {
      const response = await transport.fetch(await transport.resolveUrl("/sources/instagram/task-result"), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
      });
      if (response.ok && response.status >= 200 && response.status < 300) return;
      if (response.status >= 400 && response.status < 500 && response.status !== 408 && response.status !== 429) {
        throw new InstagramTerminalResultRejection(`instagram_task_result_rejected: HTTP ${response.status}`);
      }
      failure = `HTTP ${response.status}`;
    } catch (error) {
      if (error instanceof InstagramTerminalResultRejection) throw error;
      failure = "network_error";
    }
    if (attempt < attempts) {
      await transport.sleep(Math.min(RESULT_BACKOFF_MAX_MS, RESULT_BACKOFF_MS * 2 ** (attempt - 1)));
    }
  }
  throw new Error(`instagram_task_result_unacknowledged: ${failure}`);
}

async function removeTabBestEffort(tabId: number): Promise<void> {
  try {
    // Browser restarts can reuse tab IDs, and users can navigate away while an
    // outbox waits for ACK. Never close a page that is no longer task-owned.
    const tab = await chrome.tabs.get(tabId);
    if (!ownedTaskUrl(tab.url || "")) return;
    await chrome.tabs.remove(tabId);
  } catch {
    // User closure and restart races are harmless.
  }
}

async function cleanupAfterAck(): Promise<void> {
  if (deadlineTimer !== null) clearTimeout(deadlineTimer);
  deadlineTimer = null;
  const tabId = state?.tabId;
  state = null;
  inFlight = false;
  navigationGeneration += 1;
  try {
    await chrome.alarms.clear(RESULT_RETRY_ALARM);
  } catch {
    // Best effort after authoritative ACK.
  }
  await clearPersistedState();
  if (typeof tabId === "number") await removeTabBestEffort(tabId);
  if (ownsMutex) {
    releaseDispatcherMutex(OWNER);
    ownsMutex = false;
  }
}

function scheduleResultRetry(): void {
  try {
    chrome.alarms.create(RESULT_RETRY_ALARM, { delayInMinutes: RESULT_RETRY_MINUTES });
  } catch {
    // Session state is retried on the next startup/poll wake.
  }
}

async function finalize(result: InstagramTaskResult): Promise<void> {
  if (!state) return;
  state.pendingResult = result;
  state.pendingResultBody = JSON.stringify(result);
  if (deadlineTimer !== null) clearTimeout(deadlineTimer);
  deadlineTimer = null;
  try {
    // Never POST a terminal result until its exact bytes are recoverable after
    // an MV3 worker recycle. A storage failure leaves the task/tab in place and
    // retries persistence before any network delivery.
    await persistState();
  } catch (error) {
    scheduleResultRetry();
    throw error;
  }
  try {
    await postInstagramTaskResult(result, RESULT_TRANSPORT, RESULT_ATTEMPTS, state.pendingResultBody);
  } catch (error) {
    if (error instanceof InstagramTerminalResultRejection) {
      await cleanupAfterAck();
      return;
    }
    scheduleResultRetry();
    return;
  }
  await cleanupAfterAck();
}

async function retryPendingResult(): Promise<void> {
  if (!state?.pendingResult) return;
  state.pendingResultBody ||= JSON.stringify(state.pendingResult);
  try {
    await persistState();
  } catch {
    scheduleResultRetry();
    return;
  }
  try {
    await postInstagramTaskResult(
      state.pendingResult,
      RESULT_TRANSPORT,
      RESULT_ATTEMPTS,
      state.pendingResultBody,
    );
  } catch (error) {
    if (error instanceof InstagramTerminalResultRejection) {
      await cleanupAfterAck();
      return;
    }
    scheduleResultRetry();
    return;
  }
  await cleanupAfterAck();
}

function timeoutResult(error: string): InstagramTaskResult | null {
  if (!state) return null;
  const task = state.task;
  const canRetain = state.acceptedItems.length > 0
    && (task.type === "discover" || /^\d+$/.test(state.accountId || ""));
  const items = canRetain ? state.acceptedItems : [];
  return {
    task_id: task.id,
    claim_token: task.claim_token,
    status: canRetain ? "partial" : "failed",
    items,
    scope_counts: countsForItems(task, items),
    scope_complete: task.type === "bootstrap_events"
      ? Object.fromEntries(task.scopes.map((scope) => [scope, false]))
      : { discover: false },
    ...(task.type === "bootstrap_events" && /^\d+$/.test(state.accountId || "")
      ? { account_id: state.accountId }
      : {}),
    error,
    debug: {
      last_phase: state.lastProgress?.phase || "none",
      last_page: state.lastProgress?.page || 0,
      last_item_count: state.lastProgress?.item_count || 0,
      response_observed: canRetain,
      ...(canRetain ? { terminal_evidence: "durable_partial_progress_before_timeout" } : {}),
    },
  };
}

async function handleDeadline(): Promise<void> {
  if (!state) return;
  if (state.pendingResult) return retryPendingResult();
  const now = Date.now();
  if (now < Math.min(state.idleDeadlineAt, state.absoluteDeadlineAt)) return armDeadline();
  const result = timeoutResult(
    now >= state.absoluteDeadlineAt ? "task_absolute_timeout" : "task_idle_timeout",
  );
  if (result) await finalize(result);
}

function armDeadline(): void {
  if (deadlineTimer !== null) clearTimeout(deadlineTimer);
  deadlineTimer = null;
  if (!state || state.pendingResult) return;
  const deadline = Math.min(state.idleDeadlineAt, state.absoluteDeadlineAt);
  deadlineTimer = setTimeout(() => {
    deadlineTimer = null;
    void handleDeadline();
  }, Math.max(1, deadline - Date.now()));
}

const SEND_MESSAGE_RETRY_MS = 500;
const SEND_MESSAGE_MAX_ATTEMPTS = 6;

async function sendTask(): Promise<void> {
  if (!state || state.pendingResult || state.tabId === null) return;
  const expectedTaskId = state.task.id;
  try {
    const response = await chrome.tabs.sendMessage(state.tabId, {
      action: "INSTAGRAM_TASK_EXECUTE",
      data: state.task,
    }) as { ok?: boolean } | undefined;
    if (response?.ok === true && state?.task.id === expectedTaskId) {
      state.dispatchAttempts = 0;
      await persistState().catch(() => {});
      return;
    }
  } catch {
    // Content scripts can lag behind the tab-complete signal.
  }
  if (!state || state.task.id !== expectedTaskId || state.pendingResult) return;
  state.dispatchAttempts += 1;
  await persistState().catch(() => {});
  if (state.dispatchAttempts < SEND_MESSAGE_MAX_ATTEMPTS) {
    setTimeout(() => { void sendTask(); }, SEND_MESSAGE_RETRY_MS);
    return;
  }
  if (!state.reloadUsed && state.tabId !== null) {
    state.reloadUsed = true;
    state.dispatchAttempts = 0;
    await persistState().catch(() => {});
    try {
      await chrome.tabs.reload(state.tabId);
      waitForTab(state.tabId);
      return;
    } catch {
      // Fall through to the durable terminal result.
    }
  }
  const result = timeoutResult("send_message_failed_after_reload");
  if (result) await finalize(result);
}

function waitForTab(tabId: number): void {
  const generation = ++navigationGeneration;
  let finished = false;
  const run = (): void => {
    if (finished || generation !== navigationGeneration) return;
    finished = true;
    chrome.tabs.onUpdated.removeListener(listener);
    void sendTask();
  };
  const listener = (updatedId: number, info: { status?: string }): void => {
    if (updatedId === tabId && info.status === "complete") run();
  };
  chrome.tabs.onUpdated.addListener(listener);
  setTimeout(run, 12_000);
  void chrome.tabs.get(tabId).then((tab) => {
    if (tab.status === "complete") run();
  }).catch(run);
}

export type InstagramTaskExecutionDisposition = "accepted" | "declined";

export async function executeInstagramTaskInTab(
  task: InstagramTask,
  mutexAlreadyHeld = false,
): Promise<InstagramTaskExecutionDisposition> {
  if (inFlight) return "declined";
  if (!mutexAlreadyHeld && !tryAcquireDispatcherMutex(OWNER)) return "declined";
  ownsMutex = true;
  inFlight = true;
  const now = Date.now();
  state = {
    version: 1,
    task,
    tabId: null,
    lastProgress: null,
    idleDeadlineAt: now + idleTimeoutForTask(task),
    absoluteDeadlineAt: now + ABSOLUTE_TIMEOUT_MS,
    dispatchAttempts: 0,
    reloadUsed: false,
    acceptedItems: [],
    scopeCounts: Object.fromEntries(taskScopes(task).map((scope) => [scope, 0])),
    scopeComplete: Object.fromEntries(taskScopes(task).map((scope) => [scope, false])),
  };
  try {
    await persistState();
  } catch {
    // The backend claim remains lease-recoverable, but no upstream side effect
    // starts until the claim record itself is durable in local storage.
    initialPersistencePending = true;
    scheduleResultRetry();
    return "accepted";
  }
  initialPersistencePending = false;
  return startPersistedTaskTab();
}

async function startPersistedTaskTab(): Promise<InstagramTaskExecutionDisposition> {
  if (!state || state.pendingResult) return "declined";
  try {
    const tab = await chrome.tabs.create({ url: instagramTaskUrl(state.task), active: false });
    state.tabId = tab.id ?? null;
    await persistState();
  } catch {
    const result = timeoutResult("tab_create_failed");
    if (result) await finalize(result);
    return "accepted";
  }
  if (state.tabId === null) {
    const result = timeoutResult("tab_id_unknown");
    if (result) await finalize(result);
    return "accepted";
  }
  armDeadline();
  waitForTab(state.tabId);
  return "accepted";
}

function mergeProgressItems(
  task: InstagramTask,
  current: readonly InstagramWireItem[],
  incoming: unknown,
): InstagramWireItem[] {
  return itemsForTask([
    ...current,
    ...(Array.isArray(incoming) ? incoming : []),
  ], task);
}

async function rejectChangedAccount(incoming: unknown): Promise<boolean> {
  if (!state || state.task.type !== "bootstrap_events") return false;
  const accountId = string(incoming, 128);
  if (!/^\d+$/.test(accountId) || !state.accountId || state.accountId === accountId) return false;
  // A recovered execution must never relabel previously accepted personal rows.
  // Fail the entire conflicted snapshot rather than projecting either account.
  await finalize({ task_id: state.task.id, claim_token: state.task.claim_token,
    status: "failed", items: [], scope_counts: countsForItems(state.task, []),
    scope_complete: normalizedScopeComplete(state.task, {}), error: "instagram_account_changed",
    debug: { identity_resolved: false, response_observed: false } });
  return true;
}

async function applyProgress(
  progress: InstagramTaskProgress,
  sender?: chrome.runtime.MessageSender,
): Promise<void> {
  if (!state) throw new Error("instagram_task_state_unavailable");
  if (progress.task_id !== state.task.id || progress.claim_token !== state.task.claim_token) {
    throw new Error("instagram_task_progress_identity_mismatch");
  }
  if (!instagramTaskSenderMatches(state.tabId, sender)) {
    throw new Error("instagram_task_progress_sender_mismatch");
  }
  if (!["identity", "discover", "bootstrap"].includes(progress.phase)) {
    throw new Error("instagram_task_progress_invalid");
  }
  if (state.pendingResult) {
    await persistState();
    return;
  }
  if (await rejectChangedAccount(progress.account_id)) {
    throw new Error("instagram_account_changed");
  }
  state.lastProgress = {
    task_id: state.task.id,
    claim_token: state.task.claim_token,
    phase: progress.phase,
    ...(progress.scope && isKnownInstagramScope(progress.scope) ? { scope: progress.scope } : {}),
    ...(positiveInteger(progress.page, 100) ? { page: positiveInteger(progress.page, 100)! } : {}),
    ...(typeof progress.item_count === "number"
      ? { item_count: Math.max(0, Math.min(MAX_ITEMS, Math.floor(progress.item_count))) }
      : {}),
    ...(string(progress.cursor, 1024) ? { cursor: string(progress.cursor, 1024) } : {}),
    accepted: progress.accepted === true,
  };
  state.acceptedItems = mergeProgressItems(state.task, state.acceptedItems, progress.items);
  state.scopeCounts = countsForItems(state.task, state.acceptedItems);
  const incomingComplete = normalizedScopeComplete(state.task, progress.scope_complete);
  for (const scope of taskScopes(state.task)) {
    if (incomingComplete[scope]) state.scopeComplete[scope] = true;
  }
  if (state.task.type === "bootstrap_events" && /^\d+$/.test(string(progress.account_id, 128))) {
    state.accountId ||= string(progress.account_id, 128);
  }
  const progressDebug = safeDebug(progress.debug);
  if (progressDebug) state.progressDebug = progressDebug;
  if (progress.accepted === true) {
    state.idleDeadlineAt = Math.min(Date.now() + idleTimeoutForTask(state.task), state.absoluteDeadlineAt);
    armDeadline();
  }
  await persistState();
}

function mergeResultWithProgress(
  task: InstagramTask,
  result: InstagramTaskResult,
  persisted: PersistedState,
): InstagramTaskResult {
  const items = mergeProgressItems(task, persisted.acceptedItems, result.items);
  if (items.length === result.items.length) return result;
  const accountId = task.type === "bootstrap_events"
    ? string(result.account_id || persisted.accountId, 128)
    : "";
  if (task.type === "bootstrap_events" && !/^\d+$/.test(accountId)) return result;
  const scopeComplete = normalizedScopeComplete(task, result.scope_complete);
  const status = result.status === "failed" ? "partial" : result.status;
  return {
    ...result,
    status,
    items,
    scope_counts: countsForItems(task, items),
    scope_complete: scopeComplete,
    ...(accountId ? { account_id: accountId } : {}),
    debug: {
      ...(result.debug || {}),
      response_observed: true,
      terminal_evidence: string(result.debug?.terminal_evidence, 1024)
        || "durable_partial_progress_merged",
    },
  };
}

async function applyResult(
  result: InstagramTaskResult,
  sender?: chrome.runtime.MessageSender,
): Promise<void> {
  if (!state) throw new Error("instagram_task_state_unavailable");
  if (result.task_id !== state.task.id || result.claim_token !== state.task.claim_token) {
    throw new Error("instagram_task_result_identity_mismatch");
  }
  if (!instagramTaskSenderMatches(state.tabId, sender)) {
    throw new Error("instagram_task_result_sender_mismatch");
  }
  if (state.pendingResult) {
    await persistState();
    return;
  }
  if (await rejectChangedAccount(result.account_id)) return;
  const safe = sanitizeInstagramTaskResult(result, state.task);
  if (!safe) throw new Error("instagram_task_result_identity_mismatch");
  await finalize(mergeResultWithProgress(state.task, safe, state));
}

export function handleInstagramTaskProgress(
  progress: InstagramTaskProgress,
  sender?: chrome.runtime.MessageSender,
): Promise<void> {
  const previous = resultMutation;
  const running = ensureInstagramTaskRecovery().then(
    () => previous.then(
      () => applyProgress(progress, sender),
      () => applyProgress(progress, sender),
    ),
  );
  resultMutation = running.catch(() => {});
  return running;
}

export function handleInstagramTaskResult(
  result: InstagramTaskResult,
  sender?: chrome.runtime.MessageSender,
): Promise<void> {
  const previous = resultMutation;
  const running = ensureInstagramTaskRecovery().then(
    () => previous.then(
      () => applyResult(result, sender),
      () => applyResult(result, sender),
    ),
  );
  resultMutation = running.catch(() => {});
  return running;
}

async function resumeRecoveredTask(): Promise<void> {
  if (!state) return;
  if (state.pendingResult) return retryPendingResult();
  if (Date.now() >= Math.min(state.idleDeadlineAt, state.absoluteDeadlineAt)) {
    return handleDeadline();
  }
  if (state.tabId === null) {
    await startPersistedTaskTab();
    return;
  }
  try {
    const tab = await chrome.tabs.get(state.tabId);
    if (!ownedTaskUrl(tab.url || "")) {
      const result = timeoutResult("recovery_tab_not_owned");
      if (result) await finalize(result);
      return;
    }
  } catch {
    const result = timeoutResult("recovery_tab_gone");
    if (result) await finalize(result);
    return;
  }
  armDeadline();
  waitForTab(state.tabId);
}

async function recoverPersistedTask(): Promise<void> {
  // Close the poll gate before storage I/O. A read error is not equivalent to
  // an absent row and must never permit a second backend claim.
  inFlight = true;
  const restored = await loadPersistedState();
  if (!restored) {
    state = null;
    inFlight = false;
    return;
  }
  // Close the claim gate before contending for the cross-source slot. If
  // another recovered source owns it, alarms retry this exact persisted task;
  // polling cannot issue a fresh Instagram claim in the meantime.
  state = restored;
  inFlight = true;
  if (state.pendingResult) return retryPendingResult();
  if (!tryAcquireDispatcherMutex(OWNER)) {
    scheduleResultRetry();
    return;
  }
  ownsMutex = true;
  await resumeRecoveredTask();
}

export function ensureInstagramTaskRecovery(): Promise<void> {
  if (recoveryPromise) return recoveryPromise;
  const running = recoverPersistedTask();
  recoveryPromise = running;
  void running.catch(() => {
    // Allow a later alarm/message to retry the storage read while `inFlight`
    // remains closed. The caller sees the rejection and must not ACK payloads.
    if (recoveryPromise === running) recoveryPromise = null;
    scheduleResultRetry();
  });
  return running;
}

async function pollOnce(): Promise<void> {
  await ensureInstagramTaskRecovery();
  if (inFlight || state?.pendingResult) return;
  if (!tryAcquireDispatcherMutex(OWNER)) {
    // Minute alarms for all sources may fire together every time. A separate
    // durable half-minute wake avoids repeatedly losing the same mutex race.
    try {
      await chrome.alarms?.create(POLL_RETRY_ALARM, { delayInMinutes: 0.5 });
    } catch {
      // The regular poll remains the fallback if alarm persistence fails.
    }
    return;
  }
  ownsMutex = true;
  try { await chrome.alarms?.clear(POLL_RETRY_ALARM); } catch { /* regular poll remains */ }
  const task = await fetchNextTask();
  if (!task) {
    releaseDispatcherMutex(OWNER);
    ownsMutex = false;
    return;
  }
  await executeInstagramTaskInTab(task, true);
}

export function pollInstagramTaskNow(): void {
  if (pollPromise) return;
  pollPromise = pollOnce().catch(() => {}).finally(() => {
    pollPromise = null;
  });
}

export function startInstagramTaskPolling(): void {
  if (typeof chrome === "undefined" || !chrome.alarms) return;
  chrome.alarms.create(POLL_ALARM, { periodInMinutes: POLL_INTERVAL_MINUTES });
}

export function handleInstagramTaskAlarm(name: string): void {
  if (name === RESULT_RETRY_ALARM) {
    void ensureInstagramTaskRecovery().then(async () => {
      if (!state) return;
      if (initialPersistencePending) {
        try {
          await persistState();
        } catch {
          scheduleResultRetry();
          return;
        }
        initialPersistencePending = false;
        await startPersistedTaskTab();
        return;
      }
      if (state.pendingResult) {
        await retryPendingResult();
        return;
      }
      if (ownsMutex) return;
      if (!tryAcquireDispatcherMutex(OWNER)) {
        scheduleResultRetry();
        return;
      }
      ownsMutex = true;
      await resumeRecoveredTask();
    }).catch(() => scheduleResultRetry());
    return;
  }
  if (name === POLL_ALARM || name === POLL_RETRY_ALARM) pollInstagramTaskNow();
}
