/**
 * OpenBiliClaw — Background Service Worker
 *
 * Receives behavior events from content scripts,
 * buffers them, and forwards to the backend API.
 *
 * Delight (surprise) notifications are delivered via WebSocket push
 * from the runtime-stream, not HTTP polling.
 */

import {
  computeActionBadge,
  flushResponseReportsUninitialized,
} from "./badge.js";
import {
  BUFFER_MAX_SIZE,
  bufferReady,
  claimBufferedEventsForFlush,
  completeInflightEvents,
  drainParkedEvents,
  enqueueEventWithDurableAck,
  getBufferLength,
  parkEvents,
  recoverParkedEventsForFlush,
  shouldFlushImmediately,
} from "./buffer.js";
import {
  startXhsTaskPolling,
  handleXhsTaskAlarm,
  handleTaskResult,
  pollXhsTaskNow,
  type XhsTaskResult,
} from "./xhs-task-dispatcher.js";
import {
  startDyTaskPolling,
  handleDyTaskAlarm,
  handleDyTaskResult,
  handleDyScopeResult,
  handleDySearchTaskResult,
  handleDyHotTaskResult,
  handleDyFeedTaskResult,
  pollDyTaskNow,
  type DyFeedResult,
  type DyHotResult,
  type DyScopeResult,
  type DySearchResult,
  type DyTaskResult,
} from "./dy-task-dispatcher.js";
import {
  startYtTaskPolling,
  handleYtTaskAlarm,
  handleYtScopeResult,
  pollYtTaskNow,
} from "./yt-task-dispatcher.js";
import {
  startZhihuTaskPolling,
  handleZhihuTaskAlarm,
  handleZhihuTaskResult,
  pollZhihuTaskNow,
} from "./zhihu-task-dispatcher.js";
import {
  startWeiboTaskPolling,
  handleWeiboTaskAlarm,
  handleWeiboTaskResult,
  pollWeiboTaskNow,
} from "./weibo-task-dispatcher.ts";
import {
  startRedditTaskPolling,
  handleRedditTaskAlarm,
  handleRedditTaskResult,
  pollRedditTaskNow,
} from "./reddit-task-dispatcher.ts";
import {
  startLinuxdoTaskPolling,
  handleLinuxdoTaskAlarm,
  handleLinuxdoTaskResult,
  ensureLinuxdoTaskRecovery,
  pollLinuxdoTaskNow,
} from "./linuxdo-task-dispatcher.ts";
import {
  startV2EXTaskPolling,
  handleV2EXTaskAlarm,
  handleV2EXScopeResult,
  ensureV2EXTaskRecovery,
  pollV2EXTaskNow,
} from "./v2ex-task-dispatcher.ts";
import {
  ensureInstagramTaskRecovery,
  handleInstagramTaskAlarm,
  handleInstagramTaskProgress,
  handleInstagramTaskResult,
  pollInstagramTaskNow,
  startInstagramTaskPolling,
} from "./instagram-task-dispatcher.ts";
import {
  startXTaskPolling,
  handleXTaskAlarm,
  pollXTaskNow,
} from "./x-task-dispatcher.ts";
import { ensureNativeSaveTaskRecovery } from "./native-save-task-runner.ts";
import {
  startBiliTaskPolling,
  handleBiliTaskAlarm,
  handleBiliTaskResult,
  pollBiliTaskNow,
  type BiliTaskResult,
} from "./bili-task-dispatcher.js";
import type { YtScopeResult } from "../content/yt/task-executor.js";
import type { ZhihuTaskResult } from "../content/zhihu/task-executor.js";
import type { WeiboTaskResult } from "../content/weibo/task-executor.ts";
import type { RedditTaskResult } from "../content/reddit/task-executor.ts";
import type { LinuxdoTaskResult } from "../content/linuxdo/task-executor.ts";
import type { V2EXScopeResult } from "../content/v2ex/task-executor.ts";
import type {
  InstagramTaskProgress,
  InstagramTaskResult,
} from "../content/instagram/task-executor.ts";
import {
  openExtensionUi,
  parseDelightBvid,
  parseNotificationBvid,
  parseCognitionUpdateId,
} from "./notifications.js";
import {
  startCookieSync,
  handleCookieSyncAlarm,
  handleCookieSyncRuntimeEvent,
} from "./cookie-sync.js";
import { handleE2ERuntimeEvent } from "./e2e-runner.ts";
// Use .ts extension so node:test's --experimental-strip-types resolver
// (which doesn't rewrite .js → .ts for source-only modules) can follow
// the import when test files load these dispatchers directly. esbuild
// bundles either extension, so production builds are unaffected.
import { apiUrl, onBackendEndpointChange, wsUrl } from "../shared/backend-endpoint.ts";
import {
  authenticatedFetch,
  clearSession,
  ensureSession,
} from "../shared/auth.ts";
import type { BehaviorEvent } from "../shared/types.js";

// The event buffer + its chrome.storage.local persistence live in ./buffer.ts
// so they survive MV3 service-worker recycling. BUFFER_MAX_SIZE is imported
// from there; flush cadence stays here.
const BUFFER_FLUSH_INTERVAL = 30_000;
const FLUSH_ALARM_NAME = "openbiliclaw-flush-events";
let eventFlushInProgress = false;
const E2E_CAPTURE_SETTLE_MS = 1_000;
// v0.3.22+: health probe before WS prevents extension-only installs
// from flooding chrome://extensions "Errors" with browser-level
// WebSocket connection failures. A failed fetch caught here is just a
// rejected promise; the WS path went through Chrome's network logger
// at error severity and got counted toward the error badge.
const HEALTH_PROBE_TIMEOUT_MS = 2_000;
// Fallback /health probe budget for pre-/api/ping backends: /health blocks on
// a live embedding probe that can take seconds when cold, so the 2s ping
// budget would misread a healthy-but-cold backend as down.
const HEALTH_FALLBACK_TIMEOUT_MS = 12_000;
// Keep backend recovery prompt. The HTTP /api/ping gate below absorbs the
// backend-down case without opening a failing WebSocket, so a fixed 1s cadence
// is cheap and avoids stale "offline" extension state after the daemon starts.
const WS_RECONNECT_DELAY = 1_000;
type PendingNotification = import("./notifications.js").PendingNotification;
type PendingCognitionUpdate = import("./notifications.js").PendingCognitionUpdate;

// ---------------------------------------------------------------------------
// HTTP helpers (recommendation & cognition — still polled)
// ---------------------------------------------------------------------------

async function acknowledgeNotificationSent(bvid: string): Promise<void> {
  if (!bvid) return;
  await authenticatedFetch(await apiUrl("/notifications/sent"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bvid }),
  });
}

async function fetchPendingNotification(): Promise<PendingNotification | null> {
  const response = await authenticatedFetch(await apiUrl("/notifications/pending"), {
    method: "GET",
  });
  if (!response.ok) {
    throw new Error(`pending notifications failed: ${response.status}`);
  }
  const payload = (await response.json()) as { item?: PendingNotification | null };
  return payload.item ?? null;
}

async function fetchPendingCognitionUpdate(): Promise<PendingCognitionUpdate | null> {
  const response = await authenticatedFetch(await apiUrl("/cognition-updates/pending"), {
    method: "GET",
  });
  if (!response.ok) {
    throw new Error(`pending cognition updates failed: ${response.status}`);
  }
  const payload = (await response.json()) as { item?: PendingCognitionUpdate | null };
  return payload.item ?? null;
}

async function acknowledgeCognitionUpdateSeen(id: string): Promise<void> {
  if (!id) return;
  await authenticatedFetch(await apiUrl("/cognition-updates/seen"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
}

// ---------------------------------------------------------------------------
// Delight ACK (HTTP POST after WS push triggers notification)
// ---------------------------------------------------------------------------

async function acknowledgeDelightSent(bvid: string): Promise<void> {
  if (!bvid) return;
  await authenticatedFetch(await apiUrl("/delight/sent"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bvid }),
  });
}

// ---------------------------------------------------------------------------
// Polling — recommendation & cognition only (delight is WS-pushed)
// ---------------------------------------------------------------------------

/**
 * v0.3.16+: OS-level Chrome toasts are disabled by user request.
 *
 * The popup / side panel already surfaces every recommendation,
 * cognition update, delight candidate and interest probe — duplicating
 * them as Chrome toasts at the bottom-right of the screen is intrusive
 * (and tripped a recurring "Unable to download all specified images"
 * Chromium bug that polluted the service-worker console for weeks).
 *
 * We still poll ``/api/notifications/pending`` and call the ack
 * endpoints so the backend's pending queue drains. Functionally this
 * just hides the OS toast surface; popup state is unchanged.
 */
async function checkPendingNotification(): Promise<void> {
  try {
    const item = await fetchPendingNotification();
    if (item?.bvid) {
      await acknowledgeNotificationSent(item.bvid);
      return;
    }
    const cognition = await fetchPendingCognitionUpdate();
    if (cognition?.id) {
      await acknowledgeCognitionUpdateSeen(cognition.id);
    }
  } catch (err) {
    console.warn(
      "[OpenBiliClaw] Pending notification ack failed:",
      err instanceof Error ? err.message : String(err),
    );
  }
}

// ---------------------------------------------------------------------------
// WebSocket — runtime stream for delight push notifications
// ---------------------------------------------------------------------------

let runtimeSocket: WebSocket | null = null;
let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;
let runtimeConnectInFlight = false;

async function handleRuntimeEvent(event: Record<string, unknown>): Promise<void> {
  if (handleCookieSyncRuntimeEvent(event)) return;

  try {
    if (await handleE2ERuntimeEvent(event, flushCapturedEventsForE2E)) return;
  } catch (err) {
    console.warn(
      "[OpenBiliClaw] Extension E2E runtime event failed:",
      err instanceof Error ? err.message : String(err),
    );
    return;
  }

  const eventType = String(event.type ?? "");

  // Guided init finished (started from any surface) → the uninitialized
  // toolbar badge must clear without waiting for the next WS reconnect.
  // refresh.pool_updated implies an initialized backend too.
  if (
    backendUninitialized &&
    (eventType === "init_completed" || eventType === "refresh.pool_updated")
  ) {
    backendUninitialized = false;
    renderActionBadge();
    if ((await recoverParkedEventsForFlush()) > 0) await flushEvents();
  }

  // Task-kick events: the backend broadcasts these from
  // /api/sources/{xhs,dy}/kick when the CLI enqueues a bootstrap
  // task. Poking the dispatcher here cuts the worst-case
  // enqueue→pickup latency from ~60s (alarm interval) to ~50ms,
  // which is what makes init's 30s collect window reliable.
  // The chrome.alarms 60s poll stays as fallback for the
  // WS-down case.
  if (eventType === "xhs_task_available") {
    pollXhsTaskNow();
    return;
  }
  if (eventType === "dy_task_available") {
    pollDyTaskNow();
    return;
  }
  if (eventType === "yt_task_available") {
    pollYtTaskNow();
    return;
  }
  if (eventType === "zhihu_task_available") {
    pollZhihuTaskNow();
    return;
  }
  if (eventType === "weibo_task_available") {
    pollWeiboTaskNow();
    return;
  }
  if (eventType === "reddit_task_available") {
    await pollRedditTaskNow();
    return;
  }
  if (eventType === "linuxdo_task_available") {
    await pollLinuxdoTaskNow();
    return;
  }
  if (eventType === "v2ex_task_available") {
    pollV2EXTaskNow();
    return;
  }
  if (eventType === "instagram_task_available") {
    pollInstagramTaskNow();
    return;
  }
  if (eventType === "x_task_available") {
    await pollXTaskNow();
    return;
  }
  if (eventType === "bili_task_available") {
    pollBiliTaskNow();
    return;
  }

  // Dev-only: lets `curl -X POST /api/extension/reload` (or the
  // openbiliclaw extension-reload CLI shim) reload the entire
  // extension after a build, so the user doesn't have to click the
  // reload icon in chrome://extensions every iteration.
  // chrome.runtime.reload() is the MV3 native API for this; no
  // permission needed.
  if (eventType === "extension_reload") {
    if (chrome?.runtime?.reload) {
      // eslint-disable-next-line no-console
      console.debug("[OpenBiliClaw] runtime-stream → chrome.runtime.reload()");
      chrome.runtime.reload();
    }
    return;
  }

  // v0.3.16+: OS-level Chrome toasts are disabled by user request.
  // Probe and delight events surface inside the
  // popup via its own runtime-stream WS handler — no chrome
  // notification toast at the bottom-right of the screen.
  if (eventType === "interest.probe" || eventType === "avoidance.probe") {
    return;
  }

  if (eventType !== "delight.candidate") return;

  const bvid = String(event.bvid ?? "");
  if (!bvid) return;

  // Still ack the backend so the same bvid isn't re-pushed forever.
  void acknowledgeDelightSent(bvid);
}

async function flushCapturedEventsForE2E(): Promise<void> {
  await new Promise<void>((resolve) => setTimeout(resolve, E2E_CAPTURE_SETTLE_MS));
  await flushEvents();
}

async function isBackendAlive(): Promise<boolean> {
  // Gate the WS attempt on a cheap HTTP probe. A caught fetch rejection
  // doesn't get logged at error severity, so chrome://extensions stays
  // clean when the user installs the extension before starting the
  // daemon. Once the probe passes, we open the WS as before.
  //
  // Probe /api/ping, not /api/health: health awaits a live embedding probe
  // that can take seconds when cold, which used to blow the 2s budget here
  // and badge the backend as offline while it was up. A 404 means an older
  // backend without /api/ping — fall back to /health with a longer leash.
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), HEALTH_PROBE_TIMEOUT_MS);
    try {
      const resp = await fetch(await apiUrl("/ping"), {
        method: "GET",
        signal: ctrl.signal,
      });
      if (resp.status !== 404) return resp.ok;
    } finally {
      clearTimeout(timer);
    }
  } catch {
    return false;
  }
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), HEALTH_FALLBACK_TIMEOUT_MS);
    try {
      const resp = await fetch(await apiUrl("/health"), {
        method: "GET",
        signal: ctrl.signal,
      });
      return resp.ok;
    } finally {
      clearTimeout(timer);
    }
  } catch {
    return false;
  }
}

let backendReachable: boolean | null = null;
let backendUninitialized = false;

function renderActionBadge(): void {
  // Subtle "!" badge so a fresh-install user (or anyone whose daemon
  // crashed) sees the toolbar icon flag the issue without opening the
  // popup. Gray = backend unreachable; orange = reachable but guided init
  // never completed — previously that state cleared the badge and was
  // visually identical to a healthy backend, so fresh installs got zero
  // proactive signal to initialize.
  try {
    const view = computeActionBadge(backendReachable, backendUninitialized);
    void chrome.action.setBadgeText({ text: view.text });
    if (view.color) void chrome.action.setBadgeBackgroundColor({ color: view.color });
    void chrome.action.setTitle({ title: view.title });
  } catch {
    // chrome.action is missing in some contexts (e.g. tests) — best-effort.
  }
}

function setBackendBadge(reachable: boolean): void {
  backendReachable = reachable;
  // A down backend's init state is unknown; drop the stale flag so the
  // gray unreachable badge (and its hint) wins.
  if (!reachable) {
    backendUninitialized = false;
  }
  renderActionBadge();
}

async function refreshInitBadge(): Promise<void> {
  // /api/runtime-status carries `initialized` without running any billable
  // prereq probes (unlike an uninitialized /api/init-status read), so it is
  // the right cheap source for the toolbar signal. Best-effort: on any
  // failure keep the last known state.
  try {
    const response = await authenticatedFetch(await apiUrl("/runtime-status"), { method: "GET" });
    if (!response.ok) return;
    const payload = (await response.json()) as Record<string, unknown>;
    const wasUninitialized = backendUninitialized;
    backendUninitialized = payload.initialized === false;
    renderActionBadge();
    if (wasUninitialized && !backendUninitialized) {
      if ((await recoverParkedEventsForFlush()) > 0) await flushEvents();
    }
  } catch {
    // Keep the last rendered state.
  }
}

async function connectRuntimeStream(): Promise<void> {
  if (runtimeSocket !== null || runtimeConnectInFlight) return;
  runtimeConnectInFlight = true;

  try {
    if (!(await isBackendAlive())) {
      setBackendBadge(false);
      scheduleWsReconnect();
      return;
    }

    try {
      const url = await wsUrl("/runtime-stream?client=background", await ensureSession());
      runtimeSocket = new WebSocket(url);
    } catch {
      setBackendBadge(false);
      scheduleWsReconnect();
      return;
    }

    runtimeSocket.onopen = () => {
      setBackendBadge(true);
      void refreshInitBadge();
    };

    runtimeSocket.onmessage = (msg) => {
      try {
        const payload = JSON.parse(String(msg.data)) as Record<string, unknown>;
        void handleRuntimeEvent(payload).catch((err) => {
          console.warn(
            "[OpenBiliClaw] Runtime stream event failed:",
            err instanceof Error ? err.message : String(err),
          );
        });
      } catch {
        // Ignore malformed payloads.
      }
    };

    runtimeSocket.onclose = () => {
      runtimeSocket = null;
      scheduleWsReconnect();
    };

    runtimeSocket.onerror = () => {
      runtimeSocket?.close();
    };
  } finally {
    runtimeConnectInFlight = false;
  }
}

function scheduleWsReconnect(): void {
  if (wsReconnectTimer !== null) return;
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    void connectRuntimeStream();
  }, WS_RECONNECT_DELAY);
}

// ---------------------------------------------------------------------------
// Event buffer flush
// ---------------------------------------------------------------------------

async function flushEvents(): Promise<void> {
  if (eventFlushInProgress) return;
  eventFlushInProgress = true;
  try {
  await bufferReady();
  if (getBufferLength() === 0) return;

  const events = await claimBufferedEventsForFlush();
  if (events.length === 0) return;

  try {
    const response = await authenticatedFetch(await apiUrl("/events"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events }),
    });

    if (!response.ok) {
      console.warn("[OpenBiliClaw] Backend returned", response.status);
      // Keep INFLIGHT_KEY intact. The next alarm or worker restart retries the
      // exact same event IDs; merging into the capped live buffer could evict
      // an older fact.
      return;
    }
    let uninitialized = false;
    try {
      // Pre-init the backend consumes-and-drops events (200 + rejected:
      // not_initialized). Instead of dropping browsing-behavior events
      // (dwell/click/scroll) that init can never refetch, park them and drain
      // once the backend reports initialized.
      uninitialized = flushResponseReportsUninitialized(await response.json());
    } catch {
      // Non-JSON response — nothing to inspect.
    }
    if (uninitialized) {
      if (!backendUninitialized) {
        backendUninitialized = true;
        renderActionBadge();
      }
      if (await parkEvents(events)) {
        await completeInflightEvents();
        console.debug("[OpenBiliClaw] Events parked: backend not initialized yet");
      }
    } else {
      await completeInflightEvents();
      // drainParkedEvents durably writes one chunk into the live mirror before
      // shortening the parked key, so MV3 recycling can only duplicate it.
      await drainParkedEvents();
    }
    await checkPendingNotification();
  } catch {
    console.warn("[OpenBiliClaw] Backend not available, buffering events");
    // The durable inflight owner remains intact for retry.
  }
  } finally {
    eventFlushInProgress = false;
  }
}

// ---------------------------------------------------------------------------
// Alarm & lifecycle
// ---------------------------------------------------------------------------

function ensureFlushAlarm(): void {
  chrome.alarms.create(FLUSH_ALARM_NAME, {
    periodInMinutes: BUFFER_FLUSH_INTERVAL / 60_000,
  });
}

function startPlatformTaskPolling(): void {
  startXhsTaskPolling();
  startDyTaskPolling();
  startYtTaskPolling();
  startZhihuTaskPolling();
  startWeiboTaskPolling();
  startRedditTaskPolling();
  startLinuxdoTaskPolling();
  startV2EXTaskPolling();
  startInstagramTaskPolling();
  startXTaskPolling();
  startBiliTaskPolling();
}

async function startServiceWorkerAfterRecovery(): Promise<void> {
  // MV3 workers can stop between tab creation and cleanup. Source-owned recovery
  // rows close the polling gate before a new task tab can be claimed; recovery
  // never scans or closes arbitrary Reddit/X/Linux.do tabs.
  await ensureSession();
  // Runtime-stream health must not be held hostage by a runner waiting for the
  // shared task mutex. Every task wake still awaits its source recovery barrier.
  const runtimeStreamReady = connectRuntimeStream();
  await ensureLinuxdoTaskRecovery();
  await ensureNativeSaveTaskRecovery();
  await ensureV2EXTaskRecovery();
  await ensureInstagramTaskRecovery();
  await runtimeStreamReady;
  startPlatformTaskPolling();
  startCookieSync();
}

chrome.runtime.onInstalled.addListener(() => {
  ensureFlushAlarm();
  void startServiceWorkerAfterRecovery();
});

chrome.runtime.onStartup.addListener(() => {
  ensureFlushAlarm();
  void startServiceWorkerAfterRecovery();
});

chrome.action.onClicked.addListener((tab) => {
  void openExtensionUi(chrome, {
    windowId: tab.windowId,
    tab: "recommend",
  });
});

async function postXhsObservedUrls(payload: Record<string, unknown>): Promise<void> {
  try {
    await authenticatedFetch(await apiUrl("/sources/xhs/observed-urls"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    // Best-effort — missing a batch just means less enrichment coverage.
  }
}

async function postXhsTokens(
  payload: { pairs: Array<{ note_id: string; xsec_token: string }> },
): Promise<void> {
  if (!payload?.pairs || payload.pairs.length === 0) return;
  try {
    await authenticatedFetch(await apiUrl("/sources/xhs/tokens"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    // Best-effort — tokens that don't land just stay as bare URLs for now.
  }
}

async function postBangumiIdentity(payload: { uid: number; username: string }): Promise<void> {
  if (!payload || !(payload.uid > 0)) return;
  try {
    await authenticatedFetch(await apiUrl("/sources/bangumi/identity"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uid: payload.uid, username: payload.username || "" }),
    });
  } catch {
    // Best-effort — the next bgm.tv page view re-reports the identity.
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "BGM_IDENTITY_OBSERVED") {
    void postBangumiIdentity(message.data as { uid: number; username: string });
    return;
  }
  if (message.action === "XHS_URLS_OBSERVED") {
    void postXhsObservedUrls(message.data as Record<string, unknown>);
    return;
  }
  if (message.action === "XHS_TOKENS_OBSERVED") {
    void postXhsTokens(
      message.data as { pairs: Array<{ note_id: string; xsec_token: string }> },
    );
    return;
  }
  if (message.action === "XHS_TASK_RESULT") {
    void handleTaskResult(message.data as XhsTaskResult)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action === "DY_TASK_RESULT") {
    void handleDyTaskResult(message.data as DyTaskResult)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action === "DY_SCOPE_RESULT") {
    void handleDyScopeResult(message.data as DyScopeResult)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action === "DY_SEARCH_RESULT") {
    void handleDySearchTaskResult(message.data as DySearchResult)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action === "DY_HOT_RESULT") {
    void handleDyHotTaskResult(message.data as DyHotResult)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action === "DY_FEED_RESULT") {
    void handleDyFeedTaskResult(message.data as DyFeedResult)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action === "YT_SCOPE_RESULT") {
    void handleYtScopeResult(message.data as YtScopeResult)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action === "ZHIHU_TASK_RESULT") {
    void handleZhihuTaskResult(message.data as ZhihuTaskResult)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action === "WEIBO_TASK_RESULT") {
    void handleWeiboTaskResult(message.data as WeiboTaskResult)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action === "REDDIT_TASK_RESULT") {
    void handleRedditTaskResult(message.data as RedditTaskResult)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action === "LINUXDO_TASK_RESULT") {
    void handleLinuxdoTaskResult(message.data as LinuxdoTaskResult, sender.tab)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action === "V2EX_SCOPE_RESULT") {
    void handleV2EXScopeResult(message.data as V2EXScopeResult, sender)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action === "INSTAGRAM_TASK_PROGRESS") {
    void ensureInstagramTaskRecovery()
      .then(() => handleInstagramTaskProgress(message.data as InstagramTaskProgress, sender))
      .then(() => sendResponse({ ok: true }))
      .catch((error: unknown) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }
  if (message.action === "INSTAGRAM_TASK_RESULT") {
    void ensureInstagramTaskRecovery()
      .then(() => handleInstagramTaskResult(message.data as InstagramTaskResult, sender))
      .then(() => sendResponse({ ok: true }))
      .catch((error: unknown) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }
  if (message.action === "BILI_TASK_RESULT") {
    void handleBiliTaskResult(message.data as BiliTaskResult)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action !== "BEHAVIOR_EVENT") return;

  const event = message.data as BehaviorEvent;
  // Keep the message port (and therefore the MV3 worker turn) alive until the
  // chrome.storage mirror commits. HTTP flush is only a wake after that durable
  // ACK and is allowed to fail/retry independently.
  return enqueueEventWithDurableAck(event, sendResponse, (length) => {
    if (length >= BUFFER_MAX_SIZE || shouldFlushImmediately(event)) {
      void flushEvents();
    }
  });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  handleXhsTaskAlarm(alarm.name);
  handleDyTaskAlarm(alarm.name);
  handleYtTaskAlarm(alarm.name);
  handleZhihuTaskAlarm(alarm.name);
  handleWeiboTaskAlarm(alarm.name);
  void handleRedditTaskAlarm(alarm.name);
  void handleLinuxdoTaskAlarm(alarm.name);
  handleV2EXTaskAlarm(alarm.name);
  handleInstagramTaskAlarm(alarm.name);
  void handleXTaskAlarm(alarm.name);
  handleBiliTaskAlarm(alarm.name);
  if (handleCookieSyncAlarm(alarm.name)) {
    return;
  }
  if (alarm.name === FLUSH_ALARM_NAME) {
    void (async () => {
      await bufferReady();
      if (getBufferLength() === 0 && !backendUninitialized) {
        await recoverParkedEventsForFlush();
      }
      if (getBufferLength() > 0) {
        await flushEvents();
      } else {
        await checkPendingNotification();
      }
    })();
  }
});

chrome.notifications.onClicked.addListener((notificationId) => {
  if (notificationId.startsWith("openbiliclaw-probe:")) {
    void openExtensionUi(chrome, { tab: "profile" });
    void chrome.notifications.clear(notificationId);
    return;
  }
  const bvid = parseNotificationBvid(notificationId);
  if (bvid) {
    void openExtensionUi(chrome, { tab: "recommend" });
    void chrome.notifications.clear(notificationId);
    return;
  }
  const delightBvid = parseDelightBvid(notificationId);
  if (delightBvid) {
    void openExtensionUi(chrome, { tab: "recommend", delightBvid });
    void chrome.notifications.clear(notificationId);
    return;
  }
  const cognitionId = parseCognitionUpdateId(notificationId);
  if (!cognitionId) {
    return;
  }
  void openExtensionUi(chrome, { tab: "profile" });
  void chrome.notifications.clear(notificationId);
});

// Kick off the restore gate at SW start so events persisted before a recycle
// are back in the buffer for the next alarm flush, even without a fresh event.
void bufferReady();
ensureFlushAlarm();
void startServiceWorkerAfterRecovery();

// Popup writes a new backend port → chrome.storage.onChanged fires here.
// Close the existing runtime-stream WS so the next connect attempt opens
// against the new origin. All HTTP callers resolve apiUrl() at call time,
// so no further bookkeeping is needed for polled requests.
onBackendEndpointChange(() => {
  try {
    runtimeSocket?.close();
  } catch {
    // close() shouldn't throw, but we don't want a stray reset to crash
    // the service worker.
  }
  runtimeSocket = null;
  if (wsReconnectTimer !== null) {
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = null;
  }
  void clearSession().then(() => connectRuntimeStream());
});

console.log("[OpenBiliClaw] Service worker initialized");
