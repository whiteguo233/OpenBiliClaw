/**
 * Douyin task dispatcher — background polling for bootstrap_profile tasks.
 *
 * Task 5 of the Douyin bootstrap import plan
 * (docs/plans/2026-05-06-douyin-bootstrap-import.md). Module isolation:
 * zero imports from xhs-task-dispatcher; the dy/ tree owns its own
 * lifecycle so divergence is allowed.
 *
 * Polls `GET /api/sources/dy/next-task` at intervals. When the backend
 * hands out a task, the dispatcher:
 *   1. Opens a Douyin tab. Discovery tasks stay in the background;
 *      bootstrap_profile remains foreground because it imports the
 *      user's own account signals.
 *   2. Listens for `DY_TASK_RESULT` messages from the content script
 *      (partial + final).
 *   3. POSTs each result back to `/api/sources/dy/task-result`.
 *   4. Closes the tab on the final (status=ok / failed / empty) result
 *      or on timeout.
 *   5. Waits ``DEFAULT_POLL_INTERVAL_MS`` before asking for the next.
 *
 * Firefox MV3 background scripts are event pages and Chrome MV3 service
 * workers are recycled when idle, so the in-flight task snapshot (task,
 * tab, per-kind progress, deadline) is mirrored into
 * ``chrome.storage.session`` on every transition and the timeout is
 * backed by a ``chrome.alarms`` one-shot. A fresh worker restores the
 * snapshot via ``ensureDyTaskRecovery`` so late results still post, the
 * deadline backstop still closes the task tab, and orphaned marker tabs
 * are swept on wake.
 *
 * Only one task is in flight at a time (mutex). Bootstrap tasks get a
 * generous timeout because each scope can scroll up to 15 rounds and
 * we navigate through 4 scopes serially.
 */

import type {
  DouyinBootstrapItem,
  DouyinScope,
  DouyinSearchItem,
} from "../main/dy-fetch-tap.js";
import { apiUrl } from "../shared/backend-endpoint.ts";
import { authenticatedFetch } from "../shared/auth.ts";
import { isNativeSaveTask, type NativeSaveResult, type NativeSaveTask } from "../shared/native-save.ts";
import { withTaskTabMarker } from "../shared/task-tab.ts";
import { ensureNativeSaveTaskRecovery, runNativeSaveTask } from "./native-save-task-runner.ts";
import { runtimeAssetCandidates } from "../shared/asset-prefix.ts";
import { createTaskTab } from "./task-tab.ts";

const DY_TASK_MARKER = "openbiliclaw_dy_task";
// Cross-source mutex via globalThis. Mirror of the helper inlined
// in xhs-task-dispatcher; both dispatchers coordinate by writing to
// the same field on globalThis. See dispatcher-mutex.ts for the
// canonical reference (kept as documentation, not an import target).
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

// buildScopeUrl is loaded lazily via dynamic import inside the
// chrome-lifecycle code path (executeTask / navigateToCurrentScope).
// Reason: node:test's --experimental-strip-types resolver can't follow
// `.js` paths into `.ts` source files when the importer is the
// dispatcher's own .ts source. Pure-helper unit tests (buildDyTaskUrl
// / isValidDyTask / computeDyTaskTimeoutMs / buildDyExecuteMessageData)
// don't touch the chrome path so they stay testable. The bundled
// extension (esbuild) inlines the dynamic import at build time, so
// production runtime is unaffected.
async function loadBuildScopeUrl(): Promise<
  (scope: DouyinScope, secUid: string) => string
> {
  const mod = await import("../content/dy/task-executor.js");
  return mod.buildScopeUrl;
}

const DEFAULT_POLL_INTERVAL_MS = 60_000;
const TASK_TIMEOUT_MS = 30_000;
const SEARCH_TASK_TIMEOUT_MS = 180_000;
const FEED_TASK_TIMEOUT_MS = 120_000;
const BOOTSTRAP_PER_ROUND_TIMEOUT_MS = 3_000;
const BOOTSTRAP_MAX_TASK_TIMEOUT_MS = 360_000;
const POLL_ALARM_NAME = "openbiliclaw-dy-task-poll";
const TASK_TIMEOUT_ALARM_NAME = "openbiliclaw-dy-task-timeout";
export const DY_TASK_STATE_SESSION_KEY = "openbiliclaw_dy_active_task";
const KNOWN_SCOPES: readonly DouyinScope[] = [
  "dy_post",
  "dy_collect",
  "dy_like",
  "dy_follow",
] as const;

export interface DyLegacyTask {
  id: string;
  type: "bootstrap_profile" | "search" | "hot" | "feed";
  scopes?: DouyinScope[];
  max_items_per_scope?: number;
  max_scroll_rounds?: number;
  max_stagnant_scroll_rounds?: number;
  keywords?: string[];
  max_items_per_keyword?: number;
  hot_items?: DyHotTaskItem[];
  max_items_per_hot?: number;
  max_items?: number;
}

export type DyTask = DyLegacyTask | NativeSaveTask;

export interface DyHotTaskItem {
  word?: string;
  sentence_id: string;
  hot_value?: number;
  seed_aweme_id?: string;
  group_id?: string;
}

export interface DyTaskResult {
  task_id: string;
  status: "ok" | "empty" | "partial" | "degraded" | "failed";
  videos?: unknown[];
  scope_counts?: Record<string, number>;
  error?: string;
  debug?: Record<string, unknown>;
}

export type DyTaskExecutionDisposition = "accepted" | "declined";
export type DyTaskCallbackResult = DyTaskResult | NativeSaveResult;

let taskInFlight = false;
let pollInFlight: Promise<void> | null = null;
let taskTabId: number | null = null;
let ownsTaskTab = false;
let taskTimeoutId: ReturnType<typeof setTimeout> | null = null;
let taskDeadlineMs: number | null = null;
let currentTask: DyTask | null = null;

// Per-scope state machine. Bootstrap visits 4 profile sub-tabs
// serially (post → collect → like → follow); each sub-tab is its
// own URL load + DY_SCOPE_EXECUTE round-trip with the content
// script. Scope counts accumulate across sub-tabs so the final
// status=ok payload carries the full picture.
interface TaskProgress {
  task_id: string;
  scopes: DouyinScope[];
  current_scope_idx: number;
  accumulated_counts: Record<DouyinScope, number>;
  scope_statuses: Partial<Record<DouyinScope, DyScopeResult["status"]>>;
  degraded_reasons: string[];
  max_items_per_scope: number;
  max_scroll_rounds: number;
  max_stagnant_scroll_rounds: number;
}

let progress: TaskProgress | null = null;

interface SearchProgress {
  task_id: string;
  keywords: string[];
  current_keyword_idx: number;
  accumulated_count: number;
  max_items_per_keyword: number;
  navigation_resume_count: number;
  navigation_resume_dispatched: boolean;
  navigation_generation: number;
}

let searchProgress: SearchProgress | null = null;
let searchNavigationListener:
  | ((tabId: number, info: { status?: string; url?: string }, tab: chrome.tabs.Tab) => void)
  | null = null;
let searchNavigationFallbackId: ReturnType<typeof setTimeout> | null = null;

interface HotProgress {
  task_id: string;
  hot_items: DyHotTaskItem[];
  current_hot_idx: number;
  accumulated_count: number;
  max_items_per_hot: number;
  max_items_total: number;
}

let hotProgress: HotProgress | null = null;

interface FeedProgress {
  task_id: string;
  accumulated_count: number;
  max_items: number;
  capture_retry_count: number;
}

let feedProgress: FeedProgress | null = null;

// ---------------------------------------------------------------------------
// Pure helpers (testable without chrome)
// ---------------------------------------------------------------------------

export function buildDyTaskUrl(task: DyTask): string | null {
  if (task.type === "native_save") return task.content_url;
  if (task.type === "bootstrap_profile") {
    return withTaskTabMarker("https://www.douyin.com/", DY_TASK_MARKER);
  }
  if (task.type === "search") {
    return withTaskTabMarker("https://www.douyin.com/", DY_TASK_MARKER);
  }
  if (task.type === "hot") {
    return withTaskTabMarker("https://www.douyin.com/", DY_TASK_MARKER);
  }
  if (task.type === "feed") {
    return withTaskTabMarker("https://www.douyin.com/", DY_TASK_MARKER);
  }
  return null;
}

export function buildDyDiscoveryPageUrl(
  _type: "search" | "hot" | "feed",
  _target?: string,
): string {
  return withTaskTabMarker("https://www.douyin.com/", DY_TASK_MARKER);
}

export function isValidDyTask(task: unknown): task is DyTask {
  if (isNativeSaveTask(task)) {
    return task.platform === "douyin" && task.platform_slug === "dy";
  }
  if (typeof task !== "object" || task === null) return false;
  const t = task as Record<string, unknown>;
  if (typeof t.id !== "string" || !t.id) return false;
  if (t.type === "search") {
    if (!Array.isArray(t.keywords)) return false;
    return t.keywords.some((keyword) => typeof keyword === "string" && keyword.trim());
  }
  if (t.type === "hot") {
    if (!Array.isArray(t.hot_items)) return false;
    return t.hot_items.some((item) => {
      if (!item || typeof item !== "object") return false;
      const row = item as Record<string, unknown>;
      return typeof row.sentence_id === "string" && Boolean(row.sentence_id.trim());
    });
  }
  if (t.type === "feed") {
    if (t.max_items === undefined) return true;
    return typeof t.max_items === "number" && Number.isFinite(t.max_items) && t.max_items > 0;
  }
  if (t.type !== "bootstrap_profile") return false;
  if (t.scopes !== undefined) {
    if (!Array.isArray(t.scopes)) return false;
    for (const s of t.scopes) {
      if (!KNOWN_SCOPES.includes(s as DouyinScope)) return false;
    }
  }
  return true;
}

export function computeDyTaskTimeoutMs(task: DyLegacyTask): number {
  if (task.type === "search") {
    const keywordCount =
      Array.isArray(task.keywords) && task.keywords.length > 0 ? task.keywords.length : 1;
    return Math.min(
      Math.max(SEARCH_TASK_TIMEOUT_MS, keywordCount * SEARCH_TASK_TIMEOUT_MS),
      BOOTSTRAP_MAX_TASK_TIMEOUT_MS,
    );
  }
  if (task.type === "hot") {
    const hotCount =
      Array.isArray(task.hot_items) && task.hot_items.length > 0 ? task.hot_items.length : 1;
    return Math.min(
      Math.max(TASK_TIMEOUT_MS, TASK_TIMEOUT_MS + hotCount * 70_000),
      BOOTSTRAP_MAX_TASK_TIMEOUT_MS,
    );
  }
  if (task.type === "feed") {
    return Math.min(
      Math.max(TASK_TIMEOUT_MS, FEED_TASK_TIMEOUT_MS),
      BOOTSTRAP_MAX_TASK_TIMEOUT_MS,
    );
  }
  // Default per-task timeout has to account for the executor visiting
  // up to 4 scope tabs in series, each scrolling up to N rounds. We
  // assume 4 scopes if the task didn't enumerate them — the CLI's
  // default invocation does NOT pass scopes explicitly today, so
  // dropping below 4 here would silently squeeze the budget.
  const scopeCount = Array.isArray(task.scopes) && task.scopes.length > 0
    ? task.scopes.length
    : 4;
  const rounds =
    typeof task.max_scroll_rounds === "number" && Number.isFinite(task.max_scroll_rounds)
      ? Math.max(0, Math.floor(task.max_scroll_rounds))
      : 0;
  const scrollBudget = scopeCount * rounds * BOOTSTRAP_PER_ROUND_TIMEOUT_MS;
  return Math.min(
    Math.max(TASK_TIMEOUT_MS, TASK_TIMEOUT_MS + scrollBudget),
    BOOTSTRAP_MAX_TASK_TIMEOUT_MS,
  );
}

export function buildDyExecuteMessageData(task: DyLegacyTask): Record<string, unknown> {
  const data: Record<string, unknown> = { task_id: task.id, type: task.type };
  if (task.scopes !== undefined) data.scopes = task.scopes;
  if (task.max_items_per_scope !== undefined) {
    data.max_items_per_scope = task.max_items_per_scope;
  }
  if (task.max_scroll_rounds !== undefined) data.max_scroll_rounds = task.max_scroll_rounds;
  if (task.max_stagnant_scroll_rounds !== undefined) {
    data.max_stagnant_scroll_rounds = task.max_stagnant_scroll_rounds;
  }
  if (task.keywords !== undefined) data.keywords = task.keywords;
  if (task.max_items_per_keyword !== undefined) {
    data.max_items_per_keyword = task.max_items_per_keyword;
  }
  if (task.hot_items !== undefined) data.hot_items = task.hot_items;
  if (task.max_items_per_hot !== undefined) {
    data.max_items_per_hot = task.max_items_per_hot;
  }
  if (task.max_items !== undefined) data.max_items = task.max_items;
  return data;
}

export function shouldFinalizeHotTask({
  accumulatedCount,
  maxItemsTotal,
  currentHotIndex,
  hotItemCount,
}: {
  accumulatedCount: number;
  maxItemsTotal: number;
  currentHotIndex: number;
  hotItemCount: number;
}): boolean {
  return accumulatedCount >= maxItemsTotal || currentHotIndex + 1 >= hotItemCount;
}

export function shouldRetryDyFeedCapture(
  result: Pick<DyFeedResult, "status" | "error">,
  captureRetryCount: number,
): boolean {
  return (
    result.status === "failed" &&
    result.error === "feed_no_observed_response" &&
    captureRetryCount < 1
  );
}

export function shouldOpenDyTaskActive(task: DyLegacyTask): boolean {
  return task.type === "bootstrap_profile";
}

export function isDySearchResultUrl(urlValue: string, keyword: string): boolean {
  try {
    const url = new URL(urlValue);
    const path = decodeURIComponent(url.pathname);
    const segments = path.split("/").filter(Boolean);
    const searchIndex = segments.lastIndexOf("search");
    if (searchIndex < 0) return false;
    const expected = keyword.trim();
    if (!expected) return false;
    return (
      (segments[searchIndex + 1] ?? "") === expected ||
      url.searchParams.get("keyword") === expected ||
      url.searchParams.get("q") === expected
    );
  } catch {
    return false;
  }
}

export function finalizeDyBootstrapStatus(
  scopeStatuses: Partial<Record<DouyinScope, DyScopeResult["status"]>>,
): "ok" | "degraded" {
  return Object.values(scopeStatuses).some(
    (status) => status === "degraded" || status === "failed",
  )
    ? "degraded"
    : "ok";
}

export function dyScopeDegradedReason(result: DyScopeResult): string {
  return `${result.scope}:${result.error || result.status}`;
}

// ---------------------------------------------------------------------------
// Chrome lifecycle (Task 4's chrome-devtools MCP probe already exercised the
// highest-risk seam against real douyin.com; the event-page persistence /
// recovery paths are unit-tested in tests/dy-task-recovery.test.ts).
// ---------------------------------------------------------------------------

async function fetchNextTask(): Promise<DyTask | null> {
  try {
    const resp = await authenticatedFetch(await apiUrl("/sources/dy/next-task"));
    if (resp.status === 204) return null; // no pending task
    if (!resp.ok) return null;
    const payload: unknown = await resp.json();
    return isValidDyTask(payload) ? payload : null;
  } catch {
    return null;
  }
}

export interface DyTaskResultResponse {
  ok: boolean;
  status: number;
}

export interface DyTaskResultTransport {
  resolveUrl: (path: string) => Promise<string>;
  fetch: (input: string, init: RequestInit) => Promise<DyTaskResultResponse>;
  sleep: (delayMs: number) => Promise<void>;
}

export interface DyTaskResultRetryOptions {
  maxAttempts?: number;
  baseDelayMs?: number;
}

const DY_TASK_RESULT_MAX_ATTEMPTS = 3;
const DY_TASK_RESULT_RETRY_BASE_DELAY_MS = 250;
const DY_TASK_RESULT_RETRY_MAX_DELAY_MS = 2_000;

function delay(delayMs: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

const DY_TASK_RESULT_TRANSPORT: DyTaskResultTransport = {
  resolveUrl: apiUrl,
  fetch: authenticatedFetch,
  sleep: delay,
};

/**
 * Deliver one idempotent task callback and require a backend ACK.
 *
 * The task-result endpoint makes terminal callbacks immutable, so retrying the
 * exact JSON body is safe. Keep the retry window deliberately short and
 * bounded: it covers a transient daemon restart without turning a disconnected
 * backend into a service-worker retry storm. Callers must not clean up their
 * local task lifecycle when this function rejects.
 */
export async function postDyTaskResult(
  result: DyTaskCallbackResult,
  transport: DyTaskResultTransport = DY_TASK_RESULT_TRANSPORT,
  options: DyTaskResultRetryOptions = {},
): Promise<void> {
  const maxAttempts = Math.max(
    1,
    Math.min(5, Math.floor(options.maxAttempts ?? DY_TASK_RESULT_MAX_ATTEMPTS)),
  );
  const baseDelayMs = Math.max(
    0,
    Math.min(
      DY_TASK_RESULT_RETRY_MAX_DELAY_MS,
      Math.floor(options.baseDelayMs ?? DY_TASK_RESULT_RETRY_BASE_DELAY_MS),
    ),
  );
  const body = JSON.stringify(result);
  let lastFailure = "unknown";

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await transport.fetch(await transport.resolveUrl("/sources/dy/task-result"), {
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
      const retryDelayMs = Math.min(
        DY_TASK_RESULT_RETRY_MAX_DELAY_MS,
        baseDelayMs * 2 ** (attempt - 1),
      );
      await transport.sleep(retryDelayMs);
    }
  }

  throw new Error(`dy_task_result_unacknowledged: ${lastFailure}`);
}

async function postTaskResult(result: DyTaskResult): Promise<void> {
  await postDyTaskResult(result);
}

export interface DyNativeSaveResultTransport {
  resolveUrl: (path: string) => Promise<string>;
  fetch: (input: string, init: RequestInit) => Promise<unknown>;
}

const DY_NATIVE_SAVE_RESULT_TRANSPORT: DyNativeSaveResultTransport = {
  resolveUrl: apiUrl,
  fetch: authenticatedFetch,
};

export async function postDyNativeSaveResult(
  result: NativeSaveResult,
  transport: DyNativeSaveResultTransport = DY_NATIVE_SAVE_RESULT_TRANSPORT,
): Promise<void> {
  try {
    await transport.fetch(await transport.resolveUrl("/sources/dy/task-result"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch {
    // Backend transient unavailability should not crash the service worker.
  }
}

export interface DyNativeSaveDispatchDependencies {
  run: (
    task: NativeSaveTask,
    platformSlug: "dy",
    postResult: (result: NativeSaveResult) => Promise<void>,
  ) => Promise<void>;
  postResult: (result: NativeSaveResult) => Promise<void>;
}

/** Behavior seam used by executeTask and tests to keep native-result closure explicit. */
export async function dispatchDyNativeSaveTask(
  task: NativeSaveTask,
  dependencies: DyNativeSaveDispatchDependencies,
): Promise<void> {
  await dependencies.run(task, "dy", dependencies.postResult);
}

const DY_NATIVE_SAVE_DISPATCH_DEPENDENCIES: DyNativeSaveDispatchDependencies = {
  run: runNativeSaveTask,
  postResult: postDyNativeSaveResult,
};

function clearSearchNavigationWatcher(): void {
  if (searchNavigationListener !== null) {
    try {
      chrome.tabs.onUpdated.removeListener(searchNavigationListener);
    } catch {
      // The extension context may already be invalidated during a dev reload.
    }
    searchNavigationListener = null;
  }
  if (searchNavigationFallbackId !== null) {
    clearTimeout(searchNavigationFallbackId);
    searchNavigationFallbackId = null;
  }
}

// ---------------------------------------------------------------------------
// Active-task persistence (event page / service-worker recycling)
// ---------------------------------------------------------------------------

export interface PersistedDyTaskState {
  task: DyLegacyTask;
  task_tab_id: number | null;
  owns_task_tab: boolean;
  deadline_ms: number;
  progress: TaskProgress | null;
  search_progress: SearchProgress | null;
  hot_progress: HotProgress | null;
  feed_progress: FeedProgress | null;
}

let sessionStateMutation: Promise<void> = Promise.resolve();
let dyTaskRecoveryPromise: Promise<void> | null = null;

function sessionStorageArea(): chrome.storage.StorageArea | null {
  try {
    return typeof chrome === "undefined" ? null : chrome.storage?.session ?? null;
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
    if (!storage || !currentTask || currentTask.type === "native_save") return;
    const record: PersistedDyTaskState = {
      task: currentTask,
      task_tab_id: taskTabId,
      owns_task_tab: ownsTaskTab,
      deadline_ms: taskDeadlineMs ?? 0,
      progress,
      search_progress: searchProgress,
      hot_progress: hotProgress,
      feed_progress: feedProgress,
    };
    try {
      await storage.set({ [DY_TASK_STATE_SESSION_KEY]: record });
    } catch {
      // Session persistence is an optional recovery enhancement, not an execution prerequisite.
    }
  });
}

function clearPersistedTaskState(): Promise<void> {
  return serializedSessionMutation(async () => {
    const storage = sessionStorageArea();
    if (!storage) return;
    try {
      await storage.remove(DY_TASK_STATE_SESSION_KEY);
    } catch {
      // The in-memory lifecycle is already terminal for this worker.
    }
  });
}

function parsePersistedTaskState(value: unknown): PersistedDyTaskState | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  if (!isValidDyTask(record.task) || record.task.type === "native_save") return null;
  const task = record.task;
  if (typeof record.deadline_ms !== "number" || !Number.isFinite(record.deadline_ms)) return null;
  const tabId = record.task_tab_id ?? null;
  if (tabId !== null && (typeof tabId !== "number" || !Number.isInteger(tabId))) return null;
  const parsed: PersistedDyTaskState = {
    task,
    task_tab_id: tabId,
    owns_task_tab: record.owns_task_tab === true,
    deadline_ms: record.deadline_ms,
    progress: null,
    search_progress: null,
    hot_progress: null,
    feed_progress: null,
  };
  const progressFields = [
    ["progress", "bootstrap_profile"],
    ["search_progress", "search"],
    ["hot_progress", "hot"],
    ["feed_progress", "feed"],
  ] as const;
  let hasProgress = false;
  for (const [key, type] of progressFields) {
    const candidate = record[key];
    if (candidate === null || candidate === undefined) continue;
    if (task.type !== type) return null;
    if (
      typeof candidate !== "object" ||
      (candidate as { task_id?: unknown }).task_id !== task.id
    ) {
      return null;
    }
    if (key === "progress") parsed.progress = candidate as TaskProgress;
    if (key === "search_progress") parsed.search_progress = candidate as SearchProgress;
    if (key === "hot_progress") parsed.hot_progress = candidate as HotProgress;
    if (key === "feed_progress") parsed.feed_progress = candidate as FeedProgress;
    hasProgress = true;
  }
  return hasProgress ? parsed : null;
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
  // event page / service worker being unloaded. The setTimeout below is
  // only the exact-timing fast path while this worker stays alive.
  scheduleTaskTimeoutAlarm(deadlineMs);
  if (taskTimeoutId !== null) {
    clearTimeout(taskTimeoutId);
    taskTimeoutId = null;
  }
  taskTimeoutId = setTimeout(() => {
    taskTimeoutId = null;
    void failActiveTask("task_timeout");
  }, Math.max(0, deadlineMs - Date.now()));
}

async function failActiveTask(error: string): Promise<void> {
  const task = currentTask;
  if (!task || task.type === "native_save") return;
  try {
    await postTaskResult({
      task_id: task.id,
      status: "failed",
      error,
    });
    cleanupTask();
  } catch {
    // Keep the local task state intact after an unacknowledged terminal
    // callback. The persisted record and the timeout alarm retry the
    // callback on the next wake instead of pretending the task was
    // durably failed and claiming more.
  }
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

async function restorePersistedTaskState(record: PersistedDyTaskState): Promise<void> {
  const tabAlive = await recordedTabAlive(record.task_tab_id);
  currentTask = record.task;
  taskTabId = tabAlive ? record.task_tab_id : null;
  ownsTaskTab = record.owns_task_tab && tabAlive;
  progress = record.progress;
  searchProgress = record.search_progress;
  hotProgress = record.hot_progress;
  feedProgress = record.feed_progress;
  taskInFlight = true;
  tryAcquireDispatcherMutex("dy");
  armTaskDeadline(record.deadline_ms);
  if (Date.now() >= record.deadline_ms) {
    await failActiveTask("task_timeout");
    return;
  }
  if (record.owns_task_tab && !tabAlive) {
    // The task tab is gone (user closed it or the browser discarded it);
    // nothing can deliver further results, so settle immediately instead
    // of burning the remaining deadline budget.
    await failActiveTask("task_tab_closed");
  }
}

async function sweepOrphanDyTaskTabs(activeTabId: number | null): Promise<void> {
  if (typeof chrome === "undefined" || !chrome.tabs?.query || !chrome.tabs?.remove) return;
  let tabs: chrome.tabs.Tab[];
  try {
    tabs = await chrome.tabs.query({ url: ["*://*.douyin.com/*"] });
  } catch {
    return;
  }
  const queryMarker = `${DY_TASK_MARKER}=1`;
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

async function recoverDyTaskState(): Promise<void> {
  const storage = sessionStorageArea();
  if (storage && !taskInFlight && currentTask === null) {
    let record: PersistedDyTaskState | null = null;
    try {
      record = parsePersistedTaskState(
        (await storage.get(DY_TASK_STATE_SESSION_KEY))[DY_TASK_STATE_SESSION_KEY],
      );
    } catch {
      record = null;
    }
    if (record) await restorePersistedTaskState(record);
  }
  await sweepOrphanDyTaskTabs(taskTabId);
}

/**
 * One MV3-lifetime barrier shared by startup, poll wakes, timeout alarms,
 * and the DY_*_RESULT message handlers. A freshly woken worker restores
 * the persisted in-flight task before any of those paths inspect the
 * module state, so late results keep flowing and the deadline backstop
 * keeps ownership of the task tab.
 */
export function ensureDyTaskRecovery(): Promise<void> {
  dyTaskRecoveryPromise ??= recoverDyTaskState().catch(() => {});
  return dyTaskRecoveryPromise;
}

export function resetDyTaskStateForTest(): void {
  clearSearchNavigationWatcher();
  if (taskTimeoutId !== null) {
    clearTimeout(taskTimeoutId);
    taskTimeoutId = null;
  }
  taskDeadlineMs = null;
  taskTabId = null;
  ownsTaskTab = false;
  currentTask = null;
  progress = null;
  searchProgress = null;
  hotProgress = null;
  feedProgress = null;
  taskInFlight = false;
  dyTaskRecoveryPromise = null;
  sessionStateMutation = Promise.resolve();
  releaseDispatcherMutex("dy");
}

function cleanupTask(): void {
  clearSearchNavigationWatcher();
  if (taskTimeoutId !== null) {
    clearTimeout(taskTimeoutId);
    taskTimeoutId = null;
  }
  taskDeadlineMs = null;
  if (typeof chrome !== "undefined" && chrome.alarms?.clear) {
    try {
      void chrome.alarms.clear(TASK_TIMEOUT_ALARM_NAME);
    } catch {
      // The alarm may already have fired; clearing stays best-effort.
    }
  }
  if (ownsTaskTab && taskTabId !== null) {
    try {
      chrome.tabs.remove(taskTabId);
    } catch {
      // Tab may already be closed; ignore.
    }
  }
  taskTabId = null;
  ownsTaskTab = false;
  currentTask = null;
  progress = null;
  searchProgress = null;
  hotProgress = null;
  feedProgress = null;
  taskInFlight = false;
  releaseDispatcherMutex("dy");
  void clearPersistedTaskState();
}

function emptyScopeCounts(): Record<DouyinScope, number> {
  return { dy_post: 0, dy_collect: 0, dy_like: 0, dy_follow: 0 };
}

function armTaskTimeout(task: DyLegacyTask): void {
  armTaskDeadline(Date.now() + computeDyTaskTimeoutMs(task));
  void persistActiveTaskState();
}

// ---------------------------------------------------------------------------
// Per-scope state-machine driver
// ---------------------------------------------------------------------------

/**
 * Wait for tab.status === "complete" then run the callback. Cleans
 * itself up on first complete signal so intra-page navigations don't
 * re-trigger the handshake.
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
  void chrome.tabs.get(tabId).then((tab) => {
    if (tab.status === "complete") runOnce();
  }).catch(() => {
    // Tab may close between create/update and readiness probing. The timeout
    // path will report the task failure if no completion event arrives.
  });
}

/**
 * Send DY_SCOPE_EXECUTE to the content script for the current scope.
 * Failure to deliver (no listener / wrong URL / CSP) is converted into
 * an empty DY_SCOPE_RESULT so the state machine still advances and
 * the task eventually finalises rather than hanging until timeout.
 */
// Keep the latest injection outcome in the normal task-result diagnostics so
// browser-side failures remain distinguishable from an empty source result.
let _lastInjectStatus: string = "not_attempted";

function sendScopeExecuteMessage(): void {
  if (!progress || !taskTabId) return;
  const scope = progress.scopes[progress.current_scope_idx];
  if (!scope) return;
  void chrome.tabs
    .sendMessage(taskTabId, {
      action: "DY_SCOPE_EXECUTE",
      data: {
        task_id: progress.task_id,
        scope,
        max_items_per_scope: progress.max_items_per_scope,
        max_scroll_rounds: progress.max_scroll_rounds,
        max_stagnant_scroll_rounds: progress.max_stagnant_scroll_rounds,
        debug_inject_status: _lastInjectStatus,
      },
    })
    .catch(() => {
      // Synthesise an empty per-scope result so the state machine
      // still advances; this is what we'd see if the user landed
      // on a Douyin login wall or risk-control page where our
      // content script isn't allowed to register.
      void handleDyScopeResult({
        task_id: progress!.task_id,
        scope,
        items: [],
        scope_count: 0,
        status: "failed",
        error: "sendMessage_failed",
      });
    });
}

function sendSearchExecuteMessage(resumeAfterNavigation: boolean = false): void {
  if (!searchProgress || !taskTabId) return;
  const keyword = searchProgress.keywords[searchProgress.current_keyword_idx];
  if (!keyword) return;
  void chrome.tabs
    .sendMessage(taskTabId, {
      action: "DY_SEARCH_EXECUTE",
      data: {
        task_id: searchProgress.task_id,
        keyword,
        max_items: searchProgress.max_items_per_keyword,
        debug_inject_status: _lastInjectStatus,
        ...(resumeAfterNavigation ? { resume_after_navigation: true } : {}),
      },
    })
    .catch((err) => {
      void handleDySearchResult({
        task_id: searchProgress!.task_id,
        keyword,
        items: [],
        scope_count: searchProgress!.accumulated_count,
        status: "failed",
        error: `sendMessage_failed: ${String(err)}`,
      });
    });
}

function dispatchSearchNavigationResume(
  tabId: number,
  keyword: string,
  generation: number,
): void {
  if (
    !searchProgress ||
    taskTabId !== tabId ||
    searchProgress.keywords[searchProgress.current_keyword_idx] !== keyword ||
    searchProgress.navigation_generation !== generation ||
    searchProgress.navigation_resume_dispatched
  ) {
    return;
  }
  searchProgress.navigation_resume_dispatched = true;
  searchProgress.navigation_resume_count += 1;
  clearSearchNavigationWatcher();
  onTabReady(
    tabId,
    () => {
      if (
        !searchProgress ||
        taskTabId !== tabId ||
        searchProgress.navigation_generation !== generation ||
        searchProgress.keywords[searchProgress.current_keyword_idx] !== keyword
      ) {
        return;
      }
      void injectFetchTapInto(tabId).then(() => {
        if (
          searchProgress &&
          taskTabId === tabId &&
          searchProgress.navigation_generation === generation &&
          searchProgress.keywords[searchProgress.current_keyword_idx] === keyword
        ) {
          sendSearchExecuteMessage(true);
        }
      });
    },
    { fallbackMs: 8_000 },
  );
}

function armSearchNavigationWatcher(tabId: number, keyword: string): void {
  clearSearchNavigationWatcher();
  if (!searchProgress) return;
  searchProgress.navigation_generation += 1;
  const generation = searchProgress.navigation_generation;
  const listener = (
    updatedId: number,
    info: { status?: string; url?: string },
    tab: chrome.tabs.Tab,
  ): void => {
    if (updatedId !== tabId) return;
    const candidateUrl = info.url ?? tab.url ?? "";
    if (!isDySearchResultUrl(candidateUrl, keyword)) return;
    dispatchSearchNavigationResume(tabId, keyword, generation);
  };
  searchNavigationListener = listener;
  chrome.tabs.onUpdated.addListener(listener);
  // Some SPA builds update history without a usable onUpdated transition.
  // Query the final tab URL once as a bounded fallback. A same-document run
  // ignores the resume via the content-side execution lock; a new document
  // accepts it and continues collection.
  searchNavigationFallbackId = setTimeout(() => {
    searchNavigationFallbackId = null;
    if (!searchProgress || taskTabId !== tabId) return;
    void chrome.tabs
      .get(tabId)
      .then((tab) => {
        if (isDySearchResultUrl(tab.url ?? "", keyword)) {
          dispatchSearchNavigationResume(tabId, keyword, generation);
        }
      })
      .catch(() => {});
  }, 10_000);
}

function sendHotExecuteMessage(): void {
  if (!hotProgress || !taskTabId) return;
  const hotItem = hotProgress.hot_items[hotProgress.current_hot_idx];
  if (!hotItem) return;
  const seedAwemeId = (hotItem.seed_aweme_id ?? hotItem.group_id ?? "").trim();
  void chrome.tabs
    .sendMessage(taskTabId, {
      action: "DY_HOT_EXECUTE",
      data: {
        task_id: hotProgress.task_id,
        sentence_id: hotItem.sentence_id,
        word: hotItem.word ?? "",
        ...(seedAwemeId ? { seed_aweme_id: seedAwemeId } : {}),
        max_items: hotProgress.max_items_per_hot,
        debug_inject_status: _lastInjectStatus,
      },
    })
    .catch((err) => {
      void handleDyHotResult({
        task_id: hotProgress!.task_id,
        sentence_id: hotItem.sentence_id,
        word: hotItem.word ?? "",
        items: [],
        scope_count: hotProgress!.accumulated_count,
        status: "failed",
        error: `sendMessage_failed: ${String(err)}`,
      });
    });
}

function sendFeedExecuteMessage(): void {
  if (!feedProgress || !taskTabId) return;
  void chrome.tabs
    .sendMessage(taskTabId, {
      action: "DY_FEED_EXECUTE",
      data: {
        task_id: feedProgress.task_id,
        max_items: feedProgress.max_items,
        debug_inject_status: _lastInjectStatus,
      },
    })
    .catch((err) => {
      void handleDyFeedResult({
        task_id: feedProgress!.task_id,
        items: [],
        scope_count: feedProgress!.accumulated_count,
        status: "failed",
        error: `sendMessage_failed: ${String(err)}`,
      });
    });
}

function navigateToCurrentSearch(): void {
  if (!searchProgress || taskTabId === null) return;
  const keyword = searchProgress.keywords[searchProgress.current_keyword_idx];
  if (!keyword) return;
  const tabId = taskTabId;
  // The tab is already ready when this function is entered. Do not update it
  // back to the same homepage: that old-document `complete` race used to let
  // the execute message land in a document that was about to unload.
  armSearchNavigationWatcher(tabId, keyword);
  void injectFetchTapInto(tabId).then(() => {
    sendSearchExecuteMessage();
  });
}

async function replaceSearchTabForNextKeyword(): Promise<void> {
  if (!searchProgress) return;
  clearSearchNavigationWatcher();
  const previousTabId = taskTabId;
  taskTabId = null;
  ownsTaskTab = false;
  if (previousTabId !== null) {
    try {
      await chrome.tabs.remove(previousTabId);
    } catch {
      // It may already have closed during navigation; continue with a fresh tab.
    }
  }

  let tab: chrome.tabs.Tab;
  try {
    tab = await createTaskTab({ url: withTaskTabMarker("https://www.douyin.com/", DY_TASK_MARKER), active: false });
  } catch (err) {
    await postTaskResult({
      task_id: searchProgress.task_id,
      status: "failed",
      error: "tab_create_failed",
      debug: { tab_create_error: String(err), stage: "next_search_keyword" },
    });
    cleanupTask();
    return;
  }
  taskTabId = tab.id ?? null;
  ownsTaskTab = true;
  if (taskTabId === null) {
    await postTaskResult({
      task_id: searchProgress.task_id,
      status: "failed",
      error: "tab_id_unknown",
      debug: { stage: "next_search_keyword" },
    });
    cleanupTask();
    return;
  }
  void persistActiveTaskState();
  const newTabId = taskTabId;
  onTabReady(
    newTabId,
    () => {
      if (searchProgress && taskTabId === newTabId) navigateToCurrentSearch();
    },
    { fallbackMs: 5_000 },
  );
}

function navigateToCurrentHot(): void {
  if (!hotProgress || taskTabId === null) return;
  const hotItem = hotProgress.hot_items[hotProgress.current_hot_idx];
  if (!hotItem) return;
  chrome.tabs.update(taskTabId, { url: buildDyDiscoveryPageUrl("hot", hotItem.sentence_id) }, () => {
    onTabReady(taskTabId!, () => {
      void injectFetchTapInto(taskTabId!).then(() => {
        sendHotExecuteMessage();
      });
    }, { fallbackMs: 10_000 });
  });
}

function navigateToFeed(): void {
  if (!feedProgress || taskTabId === null) return;
  chrome.tabs.update(taskTabId, { url: buildDyDiscoveryPageUrl("feed") }, () => {
    onTabReady(taskTabId!, () => {
      void injectFetchTapInto(taskTabId!).then(() => {
        sendFeedExecuteMessage();
      });
    }, { fallbackMs: 8_000 });
  });
}

async function reloadFeedForCaptureRetry(): Promise<void> {
  if (!feedProgress || taskTabId === null) {
    throw new Error("feed_retry_tab_missing");
  }
  const tabId = taskTabId;
  _lastInjectStatus = "feed_capture_retry_pending";
  await chrome.tabs.reload(tabId, { bypassCache: true });
  onTabReady(
    tabId,
    () => {
      void injectFetchTapInto(tabId).then(() => {
        sendFeedExecuteMessage();
      });
    },
    { fallbackMs: 10_000 },
  );
}

function navigateToCurrentScope(): void {
  // Click-driven navigation lives entirely in the content script
  // (douyin.ts: clickToScope). Dispatcher just hands it off — no
  // chrome.tabs.update, no fresh document commit, no need to re-
  // inject fetch-tap (it's still installed from the homepage stage,
  // and SPA route within Douyin's React app doesn't unload it).
  // Risk control is happier because every nav is a real-looking
  // user click instead of a URL jump.
  if (!progress || taskTabId === null) return;
  sendScopeExecuteMessage();
}

async function injectFetchTapInto(tabId: number): Promise<void> {
  // Inject dy-fetch-tap.js into the MAIN world of the current tab.
  // This bypasses the manifest content_scripts injection logic so
  // SPA-route navs and any other Chrome-version-specific edge cases
  // don't matter — every scope gets a guaranteed fresh hook.
  if (typeof chrome === "undefined" || !chrome.scripting) {
    _lastInjectStatus = "scripting_api_missing";
    return;
  }
  let lastError = "unknown injection error";
  for (const file of runtimeAssetCandidates("main/dy-fetch-tap.js")) {
    try {
      const result = await chrome.scripting.executeScript({
        target: { tabId, allFrames: false },
        files: [file],
        world: "MAIN",
      });
      _lastInjectStatus =
        `ok_file=${file};results=${Array.isArray(result) ? result.length : "n/a"}`;
      return;
    } catch (err) {
      // Firefox structured-clones the completion value of a MAIN-world file
      // injection and rejects a non-clonable result even though the script
      // executed fine (only the result clone failed). Treat that as success.
      if (String(err).includes("non-structured-clonable")) {
        _lastInjectStatus = `ok_file=${file};uncloneable_result`;
        return;
      }
      lastError = String(err);
    }
  }
  // Inject failed — could be a mismatched unpacked layout, scripting
  // permission missing, captcha intermediate page, or chrome:// blocked.
  // Capture the final error so the content script can ship it through debug.
  _lastInjectStatus = `error: ${lastError.slice(0, 120)}`;
}

function normalizeHotTaskItems(items: DyHotTaskItem[] | undefined): DyHotTaskItem[] {
  const seen = new Set<string>();
  const result: DyHotTaskItem[] = [];
  for (const item of items ?? []) {
    const sentenceId = String(item?.sentence_id ?? "").trim();
    if (!sentenceId || seen.has(sentenceId)) continue;
    seen.add(sentenceId);
    const seedAwemeId = String(item.seed_aweme_id ?? item.group_id ?? "").trim();
    result.push({
      sentence_id: sentenceId,
      word: String(item.word ?? "").trim(),
      hot_value: item.hot_value,
      ...(seedAwemeId ? { seed_aweme_id: seedAwemeId } : {}),
    });
  }
  return result.sort((a, b) => Number(Boolean(b.seed_aweme_id)) - Number(Boolean(a.seed_aweme_id)));
}

export async function executeTask(
  task: DyTask,
  nativeDependencies: DyNativeSaveDispatchDependencies = DY_NATIVE_SAVE_DISPATCH_DEPENDENCIES,
  mutexAlreadyHeld: boolean = false,
): Promise<DyTaskExecutionDisposition> {
  if (task.type === "native_save") {
    if (taskInFlight) return "declined";
    taskInFlight = true;
    try {
      await dispatchDyNativeSaveTask(task, nativeDependencies);
    } finally {
      taskInFlight = false;
    }
    return "accepted";
  }
  if (taskInFlight) return "declined";
  // Direct callers acquire here. pollDyTaskOnce acquires before claiming
  // from the backend so a busy sibling dispatcher cannot strand an already
  // claimed Douyin task in ``in_progress``.
  if (!mutexAlreadyHeld && !tryAcquireDispatcherMutex("dy")) return "declined";
  taskInFlight = true;
  currentTask = task;

  if (task.type === "search") {
    const keywords = (task.keywords ?? [])
      .map((keyword) => String(keyword).trim())
      .filter((keyword, index, all) => keyword && all.indexOf(keyword) === index);
    searchProgress = {
      task_id: task.id,
      keywords,
      current_keyword_idx: 0,
      accumulated_count: 0,
      max_items_per_keyword: Math.max(1, Math.floor(task.max_items_per_keyword ?? 20)),
      navigation_resume_count: 0,
      navigation_resume_dispatched: false,
      navigation_generation: 0,
    };

    let tab: chrome.tabs.Tab;
    try {
      tab = await createTaskTab({
        url: withTaskTabMarker("https://www.douyin.com/", DY_TASK_MARKER),
        active: shouldOpenDyTaskActive(task),
      });
    } catch (err) {
      armTaskTimeout(task);
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: "tab_create_failed",
        debug: { tab_create_error: String(err) },
      });
      cleanupTask();
      return "accepted";
    }
    taskTabId = tab.id ?? null;
    ownsTaskTab = true;
    armTaskTimeout(task);
    if (taskTabId === null || keywords.length === 0) {
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: taskTabId === null ? "tab_id_unknown" : "missing_keywords",
      });
      cleanupTask();
      return "accepted";
    }
    onTabReady(taskTabId, () => {
      navigateToCurrentSearch();
    }, { fallbackMs: 5_000 });
    return "accepted";
  }

  if (task.type === "hot") {
    const hotItems = normalizeHotTaskItems(task.hot_items);
    const maxItemsTotal = Math.max(1, Math.floor(task.max_items ?? task.max_items_per_hot ?? 20));
    hotProgress = {
      task_id: task.id,
      hot_items: hotItems,
      current_hot_idx: 0,
      accumulated_count: 0,
      max_items_per_hot: Math.max(
        1,
        Math.min(maxItemsTotal, Math.floor(task.max_items_per_hot ?? maxItemsTotal)),
      ),
      max_items_total: maxItemsTotal,
    };

    let tab: chrome.tabs.Tab;
    try {
      tab = await createTaskTab({
        url: withTaskTabMarker("https://www.douyin.com/", DY_TASK_MARKER),
        active: shouldOpenDyTaskActive(task),
      });
    } catch (err) {
      armTaskTimeout(task);
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: "tab_create_failed",
        debug: { tab_create_error: String(err) },
      });
      cleanupTask();
      return "accepted";
    }
    taskTabId = tab.id ?? null;
    ownsTaskTab = true;
    armTaskTimeout(task);
    if (taskTabId === null || hotItems.length === 0) {
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: taskTabId === null ? "tab_id_unknown" : "missing_hot_items",
      });
      cleanupTask();
      return "accepted";
    }
    onTabReady(taskTabId, () => {
      navigateToCurrentHot();
    }, { fallbackMs: 5_000 });
    return "accepted";
  }

  if (task.type === "feed") {
    feedProgress = {
      task_id: task.id,
      accumulated_count: 0,
      max_items: Math.max(1, Math.floor(task.max_items ?? 20)),
      capture_retry_count: 0,
    };

    let tab: chrome.tabs.Tab;
    try {
      tab = await createTaskTab({
        url: withTaskTabMarker("https://www.douyin.com/", DY_TASK_MARKER),
        active: shouldOpenDyTaskActive(task),
      });
    } catch (err) {
      armTaskTimeout(task);
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: "tab_create_failed",
        debug: { tab_create_error: String(err) },
      });
      cleanupTask();
      return "accepted";
    }
    taskTabId = tab.id ?? null;
    ownsTaskTab = true;
    armTaskTimeout(task);
    if (taskTabId === null) {
      await postTaskResult({
        task_id: task.id,
        status: "failed",
        error: "tab_id_unknown",
      });
      cleanupTask();
      return "accepted";
    }
    onTabReady(taskTabId, () => {
      navigateToFeed();
    }, { fallbackMs: 5_000 });
    return "accepted";
  }

  const scopes: DouyinScope[] =
    task.scopes && task.scopes.length > 0
      ? task.scopes
      : ["dy_post", "dy_collect", "dy_like", "dy_follow"];
  progress = {
    task_id: task.id,
    scopes,
    current_scope_idx: 0,
    accumulated_counts: emptyScopeCounts(),
    scope_statuses: {},
    degraded_reasons: [],
    max_items_per_scope: task.max_items_per_scope ?? 300,
    max_scroll_rounds: task.max_scroll_rounds ?? 15,
    max_stagnant_scroll_rounds: task.max_stagnant_scroll_rounds ?? 5,
  };

  // Open the Douyin homepage first instead of jumping straight to
  // /user/self. Direct profile-URL nav from a fresh tab tripped
  // Douyin's risk control on real-browser e2e (2026-05-08): user
  // saw the captcha intermediate page even when logged in. Routing
  // through the homepage lets page bundle / cookies / risk-score
  // settle naturally before we route to the profile, exactly the
  // way a user would land on their own profile (douyin.com → click
  // profile, not empty tab → /user/self).
  let tab: chrome.tabs.Tab;
  try {
    tab = await createTaskTab({
      url: withTaskTabMarker("https://www.douyin.com/", DY_TASK_MARKER),
      active: shouldOpenDyTaskActive(task),
    });
  } catch (err) {
    armTaskTimeout(task);
    await postTaskResult({
      task_id: task.id,
      status: "failed",
      error: "tab_create_failed",
      debug: { tab_create_error: String(err) },
    });
    cleanupTask();
    return "accepted";
  }
  taskTabId = tab.id ?? null;
  ownsTaskTab = true;
  armTaskTimeout(task);

  if (taskTabId === null) {
    await postTaskResult({
      task_id: task.id,
      status: "failed",
      error: "tab_id_unknown",
    });
    cleanupTask();
    return "accepted";
  }

  // Single-stage entry now — we land on douyin.com home, inject
  // fetch-tap once into MAIN world, then hand control to the
  // content-script's runScope. runScope clicks "我" then the
  // requested sub-tab (clickToScope), staying inside Douyin's SPA
  // session the whole time. No more chrome.tabs.update between
  // scopes; fetch-tap stays installed across SPA routes.
  onTabReady(taskTabId, () => {
    void injectFetchTapInto(taskTabId!).then(() => {
      sendScopeExecuteMessage();
    });
  });
  return "accepted";
}

/**
 * Per-scope result from the content script. Accumulates into the
 * task-level progress, posts a partial to the backend so memory
 * propagation happens incrementally, then either advances to the
 * next scope or finalises the task with status=ok.
 */
export async function handleDyScopeResult(result: DyScopeResult): Promise<void> {
  await ensureDyTaskRecovery();
  if (!progress || result.task_id !== progress.task_id) return;
  // Reject results from outside the current scope (defensive; the
  // content script should only emit for the scope we asked it to).
  const expectedScope = progress.scopes[progress.current_scope_idx];
  if (result.scope !== expectedScope) return;

  progress.accumulated_counts[result.scope] = result.scope_count;
  progress.scope_statuses[result.scope] = result.status;
  if (result.status === "degraded" || result.status === "failed") {
    progress.degraded_reasons.push(dyScopeDegradedReason(result));
  }

  // Post the per-scope items as a partial so the backend's
  // dy_bootstrap_videos_to_events helper propagates them through
  // memory before we move on. Mirrors the wire shape that
  // test_api_dy_ingest.py exercises end-to-end.
  await postTaskResult({
    task_id: progress.task_id,
    status: "partial",
    videos: result.items,
    scope_counts: { ...progress.accumulated_counts },
    debug: {
      scope: result.scope,
      scope_status: result.status,
      ...(result.debug ?? {}),
    },
  });

  progress.current_scope_idx += 1;
  if (progress.current_scope_idx < progress.scopes.length) {
    void persistActiveTaskState();
    navigateToCurrentScope();
    return;
  }

  // All scopes done — finalise.
  await postTaskResult({
    task_id: progress.task_id,
    status: finalizeDyBootstrapStatus(progress.scope_statuses),
    videos: [],
    scope_counts: { ...progress.accumulated_counts },
    debug: {
      scope_statuses: { ...progress.scope_statuses },
      degraded_reasons: [...progress.degraded_reasons],
    },
  });
  cleanupTask();
}

export async function handleDySearchResult(result: DySearchResult): Promise<void> {
  await ensureDyTaskRecovery();
  if (!searchProgress || result.task_id !== searchProgress.task_id) return;
  const expectedKeyword = searchProgress.keywords[searchProgress.current_keyword_idx];
  if (result.keyword !== expectedKeyword) return;
  // Invalidate any navigation-ready callback that was armed for this
  // keyword before awaiting the durable partial/final result POST.
  searchProgress.navigation_generation += 1;
  clearSearchNavigationWatcher();

  if (result.status === "failed") {
    await postTaskResult({
      task_id: searchProgress.task_id,
      status: "failed",
      error: result.error || "search_failed",
      debug: {
        search_navigation_resumes: searchProgress.navigation_resume_count,
        ...(result.debug ?? {}),
      },
    });
    cleanupTask();
    return;
  }

  searchProgress.accumulated_count += result.items.length;
  await postTaskResult({
    task_id: searchProgress.task_id,
    status: "partial",
    videos: result.items,
    scope_counts: { dy_search: searchProgress.accumulated_count },
    debug: {
      keyword: result.keyword,
      keyword_status: result.status,
      search_navigation_resumes: searchProgress.navigation_resume_count,
      ...(result.debug ?? {}),
    },
  });

  searchProgress.current_keyword_idx += 1;
  if (searchProgress.current_keyword_idx < searchProgress.keywords.length) {
    searchProgress.navigation_resume_dispatched = false;
    void persistActiveTaskState();
    await replaceSearchTabForNextKeyword();
    return;
  }

  await postTaskResult({
    task_id: searchProgress.task_id,
    status: "ok",
    videos: [],
    scope_counts: { dy_search: searchProgress.accumulated_count },
  });
  cleanupTask();
}

export async function handleDyHotResult(result: DyHotResult): Promise<void> {
  await ensureDyTaskRecovery();
  if (!hotProgress || result.task_id !== hotProgress.task_id) return;
  const expected = hotProgress.hot_items[hotProgress.current_hot_idx];
  if (!expected || result.sentence_id !== expected.sentence_id) return;

  const terminalFailure = buildDyHotTerminalFailure(result);
  if (terminalFailure) {
    await postTaskResult(terminalFailure);
    cleanupTask();
    return;
  }

  hotProgress.accumulated_count += result.items.length;
  await postTaskResult({
    task_id: hotProgress.task_id,
    status: "partial",
    videos: result.items,
    scope_counts: { dy_hot: hotProgress.accumulated_count },
    debug: {
      sentence_id: result.sentence_id,
      word: result.word,
      hot_status: result.status,
      ...(result.debug ?? {}),
      ...(result.error ? { error: result.error } : {}),
    },
  });

  if (
    shouldFinalizeHotTask({
      accumulatedCount: hotProgress.accumulated_count,
      maxItemsTotal: hotProgress.max_items_total,
      currentHotIndex: hotProgress.current_hot_idx,
      hotItemCount: hotProgress.hot_items.length,
    })
  ) {
    await postTaskResult({
      task_id: hotProgress.task_id,
      status: "ok",
      videos: [],
      scope_counts: { dy_hot: hotProgress.accumulated_count },
    });
    cleanupTask();
    return;
  }

  hotProgress.current_hot_idx += 1;
  if (hotProgress.current_hot_idx < hotProgress.hot_items.length) {
    void persistActiveTaskState();
    navigateToCurrentHot();
    return;
  }

  await postTaskResult({
    task_id: hotProgress.task_id,
    status: "ok",
    videos: [],
    scope_counts: { dy_hot: hotProgress.accumulated_count },
  });
  cleanupTask();
}

export async function handleDyFeedResult(result: DyFeedResult): Promise<void> {
  await ensureDyTaskRecovery();
  if (!feedProgress || result.task_id !== feedProgress.task_id) return;

  if (result.status === "failed") {
    if (shouldRetryDyFeedCapture(result, feedProgress.capture_retry_count)) {
      feedProgress.capture_retry_count += 1;
      void persistActiveTaskState();
      try {
        await reloadFeedForCaptureRetry();
        return;
      } catch (err) {
        await postTaskResult({
          task_id: feedProgress.task_id,
          status: "failed",
          error: "feed_capture_reload_failed",
          debug: {
            ...(result.debug ?? {}),
            feed_capture_retries: feedProgress.capture_retry_count,
            feed_reload_error: String(err),
          },
        });
        cleanupTask();
        return;
      }
    }
    await postTaskResult({
      task_id: feedProgress.task_id,
      status: "failed",
      error: result.error || "feed_failed",
      debug: {
        ...(result.debug ?? {}),
        feed_capture_retries: feedProgress.capture_retry_count,
      },
    });
    cleanupTask();
    return;
  }

  feedProgress.accumulated_count += result.items.length;
  await postTaskResult({
    task_id: feedProgress.task_id,
    status: "partial",
    videos: result.items,
    scope_counts: { dy_feed: feedProgress.accumulated_count },
    debug: {
      feed_status: result.status,
      feed_capture_retries: feedProgress.capture_retry_count,
      ...(result.debug ?? {}),
    },
  });

  await postTaskResult({
    task_id: feedProgress.task_id,
    status: "ok",
    videos: [],
    scope_counts: { dy_feed: feedProgress.accumulated_count },
  });
  cleanupTask();
}

/**
 * Legacy single-shot result handler — retained so the existing
 * service-worker.ts DY_TASK_RESULT branch keeps working for any
 * caller that posts a final result directly (e.g. tests / future
 * non-bootstrap task types). Bootstrap now flows through
 * handleDyScopeResult instead.
 */
export async function handleTaskResult(result: DyTaskResult): Promise<void> {
  await ensureDyTaskRecovery();
  if (!currentTask || result.task_id !== currentTask.id) return;
  await postTaskResult(result);
  if (result.status === "partial") return;
  cleanupTask();
}

// Per-scope wire type — content script → dispatcher.
export interface DyScopeResult {
  task_id: string;
  scope: DouyinScope;
  items: DouyinBootstrapItem[];
  scope_count: number;
  status: "ok" | "empty" | "degraded" | "failed";
  error?: string;
  debug?: Record<string, unknown>;
}

export interface DySearchResult {
  task_id: string;
  keyword: string;
  items: DouyinSearchItem[];
  scope_count: number;
  status: "ok" | "empty" | "failed";
  error?: string;
  debug?: Record<string, unknown>;
}

export interface DyHotResult {
  task_id: string;
  sentence_id: string;
  word: string;
  items: DouyinSearchItem[];
  scope_count: number;
  status: "ok" | "empty" | "failed";
  error?: string;
  debug?: Record<string, unknown>;
}

export function buildDyHotTerminalFailure(result: DyHotResult): DyTaskResult | null {
  if (result.status !== "failed") return null;
  return {
    task_id: result.task_id,
    status: "failed",
    error: result.error || "hot_failed",
    debug: result.debug,
  };
}

export interface DyFeedResult {
  task_id: string;
  items: DouyinSearchItem[];
  scope_count: number;
  status: "ok" | "empty" | "failed";
  error?: string;
  debug?: Record<string, unknown>;
}

export interface DyTaskPollDependencies {
  ensureRecovery: () => Promise<void>;
  canExecute?: () => boolean;
  fetchTask: () => Promise<DyTask | null>;
  execute: (
    task: DyTask,
    mutexAlreadyHeld: boolean,
  ) => Promise<DyTaskExecutionDisposition>;
  reportDeclined: (task: DyTask) => Promise<void>;
}

async function reportDeclinedTask(task: DyTask): Promise<void> {
  if (task.type === "native_save") {
    await postDyTaskResult({
      task_id: task.id,
      item_key: task.item_key,
      status: "failed",
      error_code: "native_save_failed",
      error_message: "dispatcher_busy_after_claim",
    });
    return;
  }
  await postTaskResult({
    task_id: task.id,
    status: "failed",
    error: "dispatcher_busy_after_claim",
  });
}

const DY_TASK_POLL_DEPENDENCIES: DyTaskPollDependencies = {
  ensureRecovery: async () => {
    await ensureNativeSaveTaskRecovery();
    await ensureDyTaskRecovery();
  },
  canExecute: () => {
    try {
      return typeof globalThis.chrome?.tabs?.create === "function";
    } catch {
      return false;
    }
  },
  fetchTask: fetchNextTask,
  execute: async (task, mutexAlreadyHeld) => {
    return await executeTask(task, DY_NATIVE_SAVE_DISPATCH_DEPENDENCIES, mutexAlreadyHeld);
  },
  reportDeclined: reportDeclinedTask,
};

export function pollDyTaskOnce(
  dependencies: DyTaskPollDependencies = DY_TASK_POLL_DEPENDENCIES,
): Promise<void> {
  if (pollInFlight) return pollInFlight;
  const running = (async () => {
    await dependencies.ensureRecovery();
    if (taskInFlight) return;
    // A runtime-stream subscriber is not necessarily a fully-capable browser
    // extension worker. Never atomically claim a backend task when this
    // execution context cannot create the tab that task requires.
    if (dependencies.canExecute && !dependencies.canExecute()) return;

    // GET /next-task atomically changes pending -> in_progress. Take the
    // cross-source slot first so a busy sibling dispatcher can never make us
    // claim a task that we then abandon without a tab or terminal callback.
    if (!tryAcquireDispatcherMutex("dy")) return;
    let releaseOnExit = true;
    try {
      const task = await dependencies.fetchTask();
      if (!task) return;
      if (task.type === "native_save") {
        // Native-save owns a separate durable runner/recovery lifecycle and
        // historically does not hold the legacy discovery tab mutex.
        releaseDispatcherMutex("dy");
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
      // The legacy task lifecycle now owns the mutex; cleanupTask releases it
      // after a result, timeout, or tab/message failure.
      releaseOnExit = false;
    } finally {
      if (releaseOnExit) releaseDispatcherMutex("dy");
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
 * Set up the dy task-poll alarm. Idempotent — chrome.alarms.create
 * with an existing name overwrites the schedule. Skip in non-extension
 * environments (node:test importing the module for pure-helper tests).
 *
 * Service-worker.ts owns the global ``chrome.alarms.onAlarm``
 * listener and dispatches into ``handleDyTaskAlarm`` from there,
 * mirroring the XHS pattern. Don't register a second listener here —
 * the result would be a torrent of redundant pollNextTask invocations.
 */
export function startDyTaskPolling(): void {
  if (typeof chrome === "undefined" || !chrome.alarms) return;
  chrome.alarms.create(POLL_ALARM_NAME, {
    periodInMinutes: DEFAULT_POLL_INTERVAL_MS / 60_000,
  });
}

/**
 * Service-worker.ts's chrome.alarms.onAlarm dispatcher routes every
 * fired alarm through this. We only act on our own alarm names (the
 * poll tick and the per-task timeout backstop); other alarms (xhs
 * poll, cookie sync, event flush) are handled by their respective
 * modules.
 */
export function handleDyTaskAlarm(alarmName: string): void {
  if (alarmName === POLL_ALARM_NAME) {
    void pollDyTaskOnce().catch(() => {});
    return;
  }
  if (alarmName === TASK_TIMEOUT_ALARM_NAME) {
    void (async () => {
      await ensureDyTaskRecovery();
      if (!currentTask || currentTask.type === "native_save" || taskDeadlineMs === null) return;
      if (Date.now() < taskDeadlineMs) {
        // The browser clamped the one-shot alarm to its minimum delay;
        // re-arm the in-process fast path for the remaining budget.
        armTaskDeadline(taskDeadlineMs);
        return;
      }
      await failActiveTask("task_timeout");
    })().catch(() => {});
  }
}

/**
 * Trigger an immediate poll. Used by the runtime-stream WebSocket
 * handler when the backend broadcasts ``dy_task_available``, so a
 * freshly-enqueued bootstrap task is picked up in <100ms instead of
 * the 0–60s next-alarm wait. Idempotent: pollNextTask() short-circuits
 * if a task is already in flight.
 */
export function pollDyTaskNow(): void {
  void pollDyTaskOnce().catch(() => {});
}

/**
 * Public message handlers — service-worker.ts routes runtime messages
 * into these:
 *   - ``DY_TASK_RESULT``    → ``handleDyTaskResult`` (legacy single-shot)
 *   - ``DY_SCOPE_RESULT``   → ``handleDyScopeResult`` (per-scope; the
 *                              path bootstrap_profile actually uses)
 *
 * Renamed exports avoid colliding with the XHS module's same-named
 * symbols. The XHS task-dispatcher and the dy-task-dispatcher both
 * share the same chrome.runtime.onMessage listener in service-worker.ts;
 * each branch only acts on its own message types so they don't
 * interfere.
 */
export const handleDyTaskResult = handleTaskResult;
export const handleDySearchTaskResult = handleDySearchResult;
export const handleDyHotTaskResult = handleDyHotResult;
export const handleDyFeedTaskResult = handleDyFeedResult;
