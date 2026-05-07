/**
 * Douyin task dispatcher — background polling for bootstrap_profile tasks.
 *
 * Task 5 of the Douyin bootstrap import plan
 * (docs/plans/2026-05-06-douyin-bootstrap-import.md). Module isolation:
 * zero imports from xhs-task-dispatcher; the dy/ tree owns its own
 * lifecycle so divergence is allowed.
 *
 * Polls `GET /api/sources/dy/next-task` at intervals. When the backend
 * hands out a bootstrap task, the dispatcher:
 *   1. Opens a foreground tab at https://www.douyin.com/.
 *   2. Listens for `DY_TASK_RESULT` messages from the content script
 *      (partial + final).
 *   3. POSTs each result back to `/api/sources/dy/task-result`.
 *   4. Closes the tab on the final (status=ok / failed / empty) result
 *      or on timeout.
 *   5. Waits ``DEFAULT_POLL_INTERVAL_MS`` before asking for the next.
 *
 * Only one task is in flight at a time (mutex). Bootstrap tasks get a
 * generous timeout because each scope can scroll up to 15 rounds and
 * we navigate through 4 scopes serially.
 */

import type { DouyinBootstrapItem, DouyinScope } from "../main/dy-fetch-tap.js";

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

const NEXT_TASK_URL = "http://127.0.0.1:8420/api/sources/dy/next-task";
const TASK_RESULT_URL = "http://127.0.0.1:8420/api/sources/dy/task-result";
const DEFAULT_POLL_INTERVAL_MS = 60_000;
const TASK_TIMEOUT_MS = 30_000;
const BOOTSTRAP_PER_ROUND_TIMEOUT_MS = 3_000;
const BOOTSTRAP_MAX_TASK_TIMEOUT_MS = 360_000;
const POLL_ALARM_NAME = "openbiliclaw-dy-task-poll";
const KNOWN_SCOPES: readonly DouyinScope[] = [
  "dy_post",
  "dy_collect",
  "dy_like",
  "dy_follow",
] as const;

export interface DyTask {
  id: string;
  type: "bootstrap_profile";
  scopes?: DouyinScope[];
  max_items_per_scope?: number;
  max_scroll_rounds?: number;
  max_stagnant_scroll_rounds?: number;
}

export interface DyTaskResult {
  task_id: string;
  status: "ok" | "empty" | "partial" | "failed";
  videos?: unknown[];
  scope_counts?: Record<string, number>;
  error?: string;
  debug?: Record<string, unknown>;
}

let taskInFlight = false;
let taskTabId: number | null = null;
let ownsTaskTab = false;
let taskTimeoutId: ReturnType<typeof setTimeout> | null = null;
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
  max_items_per_scope: number;
  max_scroll_rounds: number;
  max_stagnant_scroll_rounds: number;
}

let progress: TaskProgress | null = null;

// ---------------------------------------------------------------------------
// Pure helpers (testable without chrome)
// ---------------------------------------------------------------------------

export function buildDyTaskUrl(task: DyTask): string | null {
  if (task.type === "bootstrap_profile") {
    return "https://www.douyin.com/";
  }
  return null;
}

export function isValidDyTask(task: unknown): task is DyTask {
  if (typeof task !== "object" || task === null) return false;
  const t = task as Record<string, unknown>;
  if (typeof t.id !== "string" || !t.id) return false;
  if (t.type !== "bootstrap_profile") return false;
  if (t.scopes !== undefined) {
    if (!Array.isArray(t.scopes)) return false;
    for (const s of t.scopes) {
      if (!KNOWN_SCOPES.includes(s as DouyinScope)) return false;
    }
  }
  return true;
}

export function computeDyTaskTimeoutMs(task: DyTask): number {
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

export function buildDyExecuteMessageData(task: DyTask): Record<string, unknown> {
  const data: Record<string, unknown> = { task_id: task.id, type: task.type };
  if (task.scopes !== undefined) data.scopes = task.scopes;
  if (task.max_items_per_scope !== undefined) {
    data.max_items_per_scope = task.max_items_per_scope;
  }
  if (task.max_scroll_rounds !== undefined) data.max_scroll_rounds = task.max_scroll_rounds;
  if (task.max_stagnant_scroll_rounds !== undefined) {
    data.max_stagnant_scroll_rounds = task.max_stagnant_scroll_rounds;
  }
  return data;
}

// ---------------------------------------------------------------------------
// Chrome lifecycle (not unit-tested — Task 4's chrome-devtools MCP probe
// already exercised the highest-risk seam against real douyin.com).
// ---------------------------------------------------------------------------

async function fetchNextTask(): Promise<DyTask | null> {
  try {
    const resp = await fetch(NEXT_TASK_URL);
    if (resp.status === 204) return null; // no pending task
    if (!resp.ok) return null;
    const payload: unknown = await resp.json();
    return isValidDyTask(payload) ? payload : null;
  } catch {
    return null;
  }
}

async function postTaskResult(result: DyTaskResult): Promise<void> {
  try {
    await fetch(TASK_RESULT_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch {
    // Backend transient unavailability — drop the result rather than
    // crashing the dispatcher. The next task poll will keep things moving.
  }
}

function cleanupTask(): void {
  if (taskTimeoutId !== null) {
    clearTimeout(taskTimeoutId);
    taskTimeoutId = null;
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
  taskInFlight = false;
}

function emptyScopeCounts(): Record<DouyinScope, number> {
  return { dy_post: 0, dy_collect: 0, dy_like: 0, dy_follow: 0 };
}

function armTaskTimeout(task: DyTask): void {
  const timeoutMs = computeDyTaskTimeoutMs(task);
  taskTimeoutId = setTimeout(async () => {
    await postTaskResult({
      task_id: task.id,
      status: "failed",
      error: "task_timeout",
    });
    cleanupTask();
  }, timeoutMs);
}

// ---------------------------------------------------------------------------
// Per-scope state-machine driver
// ---------------------------------------------------------------------------

/**
 * Wait for tab.status === "complete" then run the callback. Cleans
 * itself up on first complete signal so intra-page navigations don't
 * re-trigger the handshake.
 */
function onTabReady(tabId: number, callback: () => void): void {
  const listener = (updatedId: number, info: { status?: string }): void => {
    if (updatedId !== tabId) return;
    if (info.status !== "complete") return;
    chrome.tabs.onUpdated.removeListener(listener);
    callback();
  };
  chrome.tabs.onUpdated.addListener(listener);
}

/**
 * Send DY_SCOPE_EXECUTE to the content script for the current scope.
 * Failure to deliver (no listener / wrong URL / CSP) is converted into
 * an empty DY_SCOPE_RESULT so the state machine still advances and
 * the task eventually finalises rather than hanging until timeout.
 */
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

async function navigateToCurrentScope(): Promise<void> {
  if (!progress || taskTabId === null) return;
  const scope = progress.scopes[progress.current_scope_idx];
  if (!scope) return;
  // Scope URLs intentionally use /user/self when sec_uid is unknown;
  // Douyin redirects /user/self to the logged-in user's own profile.
  const buildScopeUrl = await loadBuildScopeUrl();
  const url = buildScopeUrl(scope, "");

  // Critical: chrome.tabs.update with a same-origin same-path URL
  // (only ?showTab=… changing) is treated as an SPA route on Douyin's
  // React app — page bundle stays mounted, content_scripts MAIN-world
  // entries do NOT re-run, so dy-fetch-tap.js never reinstalls. Real
  // e2e probe 2026-05-08 caught this with install_messages_received=0
  // on scopes 3 and 4. Force a fresh document commit by routing
  // through about:blank between scopes (every nav becomes a true
  // cross-origin transition, every nav re-injects MAIN + isolated
  // content_scripts in lockstep).
  const tabId = taskTabId;
  try {
    await chrome.tabs.update(tabId, { url: "about:blank", active: true });
  } catch {
    // Tab may have been closed by the user; treat as task failure.
    void postTaskResult({
      task_id: progress.task_id,
      status: "failed",
      error: "tab_update_failed",
    });
    cleanupTask();
    return;
  }

  // Wait for about:blank commit, then push the real URL. The two-step
  // dance is what guarantees Chrome treats the second update as a
  // fresh document commit even when the second URL is same-path-as-
  // previous-scope.
  onTabReady(tabId, () => {
    void chrome.tabs
      .update(tabId, { url, active: true })
      .then(() => {
        onTabReady(tabId, sendScopeExecuteMessage);
      })
      .catch(() => {
        void postTaskResult({
          task_id: progress!.task_id,
          status: "failed",
          error: "tab_update_failed",
        });
        cleanupTask();
      });
  });
}

export async function executeTask(task: DyTask): Promise<void> {
  if (taskInFlight) return;
  taskInFlight = true;
  currentTask = task;

  const scopes: DouyinScope[] =
    task.scopes && task.scopes.length > 0
      ? task.scopes
      : ["dy_post", "dy_collect", "dy_like", "dy_follow"];
  progress = {
    task_id: task.id,
    scopes,
    current_scope_idx: 0,
    accumulated_counts: emptyScopeCounts(),
    max_items_per_scope: task.max_items_per_scope ?? 300,
    max_scroll_rounds: task.max_scroll_rounds ?? 15,
    max_stagnant_scroll_rounds: task.max_stagnant_scroll_rounds ?? 5,
  };

  const buildScopeUrl = await loadBuildScopeUrl();
  const initialUrl = buildScopeUrl(scopes[0]!, "");
  let tab: chrome.tabs.Tab;
  try {
    tab = await chrome.tabs.create({ url: initialUrl, active: true });
  } catch {
    await postTaskResult({
      task_id: task.id,
      status: "failed",
      error: "tab_create_failed",
    });
    cleanupTask();
    return;
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
    return;
  }
  onTabReady(taskTabId, sendScopeExecuteMessage);
}

/**
 * Per-scope result from the content script. Accumulates into the
 * task-level progress, posts a partial to the backend so memory
 * propagation happens incrementally, then either advances to the
 * next scope or finalises the task with status=ok.
 */
export async function handleDyScopeResult(result: DyScopeResult): Promise<void> {
  if (!progress || result.task_id !== progress.task_id) return;
  // Reject results from outside the current scope (defensive; the
  // content script should only emit for the scope we asked it to).
  const expectedScope = progress.scopes[progress.current_scope_idx];
  if (result.scope !== expectedScope) return;

  progress.accumulated_counts[result.scope] = result.scope_count;

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
    navigateToCurrentScope();
    return;
  }

  // All scopes done — finalise.
  await postTaskResult({
    task_id: progress.task_id,
    status: "ok",
    videos: [],
    scope_counts: { ...progress.accumulated_counts },
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
  status: "ok" | "empty" | "failed";
  error?: string;
  debug?: Record<string, unknown>;
}

async function pollNextTask(): Promise<void> {
  if (taskInFlight) return;
  const task = await fetchNextTask();
  if (!task) return;
  await executeTask(task);
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
 * fired alarm through this. We only act on our own alarm name; other
 * alarms (xhs poll, cookie sync, event flush) are handled by their
 * respective modules.
 */
export function handleDyTaskAlarm(alarmName: string): void {
  if (alarmName === POLL_ALARM_NAME) {
    void pollNextTask();
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
  void pollNextTask();
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
