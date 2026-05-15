/**
 * OpenBiliClaw — Background Service Worker
 *
 * Receives behavior events from content scripts,
 * buffers them, and forwards to the backend API.
 *
 * Delight (surprise) notifications are delivered via WebSocket push
 * from the runtime-stream, not HTTP polling.
 */

import { enqueueBufferedEvent, shouldFlushImmediately } from "./buffer.js";
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
import type { YtScopeResult } from "../content/yt/task-executor.js";
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
import type { BehaviorEvent } from "../shared/types.js";

let eventBuffer: BehaviorEvent[] = [];
const BUFFER_FLUSH_INTERVAL = 30_000;
const BUFFER_MAX_SIZE = 50;
const FLUSH_ALARM_NAME = "openbiliclaw-flush-events";
const BACKEND_URL = "http://localhost:8420/api/events";
const NOTIFICATION_POLL_URL = "http://127.0.0.1:8420/api/notifications/pending";
const NOTIFICATION_ACK_URL = "http://127.0.0.1:8420/api/notifications/sent";
const COGNITION_POLL_URL = "http://127.0.0.1:8420/api/cognition-updates/pending";
const COGNITION_ACK_URL = "http://127.0.0.1:8420/api/cognition-updates/seen";
const DELIGHT_ACK_URL = "http://127.0.0.1:8420/api/delight/sent";
const XHS_OBSERVED_URLS_URL = "http://127.0.0.1:8420/api/sources/xhs/observed-urls";
const XHS_TOKENS_URL = "http://127.0.0.1:8420/api/sources/xhs/tokens";
const RUNTIME_STREAM_URL = "ws://127.0.0.1:8420/api/runtime-stream?client=background";
const HEALTH_URL = "http://127.0.0.1:8420/api/health";
// v0.3.22+: health probe before WS prevents extension-only installs
// from flooding chrome://extensions "Errors" with browser-level
// WebSocket connection failures. A failed fetch caught here is just a
// rejected promise; the WS path went through Chrome's network logger
// at error severity and got counted toward the error badge.
const HEALTH_PROBE_TIMEOUT_MS = 2_000;
// v0.3.17+: exponential backoff capped at 60s. When the daemon is
// down for minutes, the previous fixed-5s reconnect flooded console
// with 12 ERR_CONNECTION_REFUSED per minute. Backoff doubles on each
// failure (5s → 10s → 20s → 40s → 60s capped); resets on successful
// onopen so transient blips stay fast-recover.
const WS_RECONNECT_BASE_DELAY = 5_000;
const WS_RECONNECT_MAX_DELAY = 60_000;
let wsReconnectDelay = WS_RECONNECT_BASE_DELAY;
type PendingNotification = import("./notifications.js").PendingNotification;
type PendingCognitionUpdate = import("./notifications.js").PendingCognitionUpdate;

// ---------------------------------------------------------------------------
// HTTP helpers (recommendation & cognition — still polled)
// ---------------------------------------------------------------------------

async function acknowledgeNotificationSent(bvid: string): Promise<void> {
  if (!bvid) return;
  await fetch(NOTIFICATION_ACK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bvid }),
  });
}

async function fetchPendingNotification(): Promise<PendingNotification | null> {
  const response = await fetch(NOTIFICATION_POLL_URL, { method: "GET" });
  if (!response.ok) {
    throw new Error(`pending notifications failed: ${response.status}`);
  }
  const payload = (await response.json()) as { item?: PendingNotification | null };
  return payload.item ?? null;
}

async function fetchPendingCognitionUpdate(): Promise<PendingCognitionUpdate | null> {
  const response = await fetch(COGNITION_POLL_URL, { method: "GET" });
  if (!response.ok) {
    throw new Error(`pending cognition updates failed: ${response.status}`);
  }
  const payload = (await response.json()) as { item?: PendingCognitionUpdate | null };
  return payload.item ?? null;
}

async function acknowledgeCognitionUpdateSeen(id: string): Promise<void> {
  if (!id) return;
  await fetch(COGNITION_ACK_URL, {
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
  await fetch(DELIGHT_ACK_URL, {
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

function handleRuntimeEvent(event: Record<string, unknown>): void {
  if (handleCookieSyncRuntimeEvent(event)) return;

  const eventType = String(event.type ?? "");

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
  // Both interest.probe and delight.candidate surface inside the
  // popup via its own runtime-stream WS handler — no chrome
  // notification toast at the bottom-right of the screen.
  if (eventType === "interest.probe") {
    return;
  }

  if (eventType !== "delight.candidate") return;

  const bvid = String(event.bvid ?? "");
  if (!bvid) return;

  // Still ack the backend so the same bvid isn't re-pushed forever.
  void acknowledgeDelightSent(bvid);
}

async function isBackendAlive(): Promise<boolean> {
  // Gate the WS attempt on a cheap HTTP probe. A caught fetch rejection
  // doesn't get logged at error severity, so chrome://extensions stays
  // clean when the user installs the extension before starting the
  // daemon. Once health passes, we open the WS as before.
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), HEALTH_PROBE_TIMEOUT_MS);
    try {
      const resp = await fetch(HEALTH_URL, { method: "GET", signal: ctrl.signal });
      return resp.ok;
    } finally {
      clearTimeout(timer);
    }
  } catch {
    return false;
  }
}

function setBackendBadge(reachable: boolean): void {
  // Subtle "!" badge so a fresh-install user (or anyone whose daemon
  // crashed) sees the toolbar icon flag the issue without opening the
  // popup. The popup itself still shows the "openbiliclaw start" hint.
  try {
    if (reachable) {
      void chrome.action.setBadgeText({ text: "" });
    } else {
      void chrome.action.setBadgeText({ text: "!" });
      void chrome.action.setBadgeBackgroundColor({ color: "#9CA3AF" });
    }
  } catch {
    // chrome.action is missing in some contexts (e.g. tests) — best-effort.
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
      runtimeSocket = new WebSocket(RUNTIME_STREAM_URL);
    } catch {
      setBackendBadge(false);
      scheduleWsReconnect();
      return;
    }

    runtimeSocket.onopen = () => {
      // v0.3.17+: reset backoff on successful connect so a transient
      // blip after a long outage still recovers immediately.
      wsReconnectDelay = WS_RECONNECT_BASE_DELAY;
      setBackendBadge(true);
    };

    runtimeSocket.onmessage = (msg) => {
      try {
        const payload = JSON.parse(String(msg.data)) as Record<string, unknown>;
        handleRuntimeEvent(payload);
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
  const delay = wsReconnectDelay;
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    void connectRuntimeStream();
  }, delay);
  // Double for next failure, capped. Resets in onopen above.
  wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_RECONNECT_MAX_DELAY);
}

// ---------------------------------------------------------------------------
// Event buffer flush
// ---------------------------------------------------------------------------

async function flushEvents(): Promise<void> {
  if (eventBuffer.length === 0) return;

  const events = [...eventBuffer];
  eventBuffer = [];

  try {
    const response = await fetch(BACKEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events }),
    });

    if (!response.ok) {
      console.warn("[OpenBiliClaw] Backend returned", response.status);
      eventBuffer.unshift(...events);
      return;
    }
    await checkPendingNotification();
  } catch {
    console.warn("[OpenBiliClaw] Backend not available, buffering events");
    eventBuffer.unshift(...events);
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

chrome.runtime.onInstalled.addListener(() => {
  ensureFlushAlarm();
  void connectRuntimeStream();
  startXhsTaskPolling();
  startDyTaskPolling();
  startYtTaskPolling();
  startCookieSync();
});

chrome.runtime.onStartup.addListener(() => {
  ensureFlushAlarm();
  void connectRuntimeStream();
  startXhsTaskPolling();
  startDyTaskPolling();
  startYtTaskPolling();
  startCookieSync();
});

chrome.action.onClicked.addListener((tab) => {
  void openExtensionUi(chrome, {
    windowId: tab.windowId,
    tab: "recommend",
  });
});

async function postXhsObservedUrls(payload: Record<string, unknown>): Promise<void> {
  try {
    await fetch(XHS_OBSERVED_URLS_URL, {
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
    await fetch(XHS_TOKENS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    // Best-effort — tokens that don't land just stay as bare URLs for now.
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
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
  if (message.action !== "BEHAVIOR_EVENT") return;

  eventBuffer = enqueueBufferedEvent(eventBuffer, message.data as BehaviorEvent, BUFFER_MAX_SIZE);

  if (eventBuffer.length >= BUFFER_MAX_SIZE || shouldFlushImmediately(message.data as BehaviorEvent)) {
    void flushEvents();
  }
});

chrome.alarms.onAlarm.addListener((alarm) => {
  handleXhsTaskAlarm(alarm.name);
  handleDyTaskAlarm(alarm.name);
  handleYtTaskAlarm(alarm.name);
  if (handleCookieSyncAlarm(alarm.name)) {
    return;
  }
  if (alarm.name === FLUSH_ALARM_NAME) {
    if (eventBuffer.length > 0) {
      void flushEvents();
      return;
    }
    void checkPendingNotification();
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

ensureFlushAlarm();
void connectRuntimeStream();
startCookieSync();

console.log("[OpenBiliClaw] Service worker initialized");
