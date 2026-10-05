import { normalizeRecommendation, normalizeSavedItem } from "./popup-helpers.js";
import { getBackendBaseUrl } from "./popup-backend-config.js";
import {
  ensurePopupSession,
  popupAuthenticatedFetch,
} from "./popup-device-auth.js";

export const CONFIG_CACHE_KEY = "openbiliclaw.config_cache";
export const CONFIG_GET_TIMEOUT_MS = 12_000;
export const CONFIG_PUT_TIMEOUT_MS = 60_000;
export const CONTENT_HISTORY_READ_TIMEOUT_MS = 12_000;
const HEALTH_SUCCESS_CACHE_TTL_MS = 3_000;
const HEALTH_FAILURE_CACHE_TTL_MS = 1_000;

let healthCacheBaseUrl = "";
let healthCacheCheckedAt = 0;
let healthCacheHasValue = false;
let healthCachePayload = null;
let healthProbeInFlight = null;

function abortError(message = "Request aborted") {
  if (typeof DOMException === "function") {
    return new DOMException(message, "AbortError");
  }
  const error = new Error(message);
  error.name = "AbortError";
  return error;
}

function withTimeout(signal, timeoutMs) {
  const hasTimeout = Number.isFinite(timeoutMs) && timeoutMs > 0;
  if (!hasTimeout && !signal) {
    return { signal: undefined, cleanup: () => {} };
  }
  if (!hasTimeout) {
    return { signal, cleanup: () => {} };
  }

  const controller = new AbortController();
  let timeoutId = null;
  const abortFrom = (reason) => {
    if (!controller.signal.aborted) {
      controller.abort(reason || abortError());
    }
  };
  const onCallerAbort = () => abortFrom(signal?.reason);

  if (signal?.aborted) {
    abortFrom(signal.reason);
  } else if (signal) {
    signal.addEventListener("abort", onCallerAbort, { once: true });
  }
  timeoutId = setTimeout(() => abortFrom(abortError("Request timed out")), timeoutMs);

  return {
    signal: controller.signal,
    cleanup: () => {
      if (timeoutId !== null) clearTimeout(timeoutId);
      if (signal) signal.removeEventListener("abort", onCallerAbort);
    },
  };
}

function awaitWithAbort(promise, signal) {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(signal.reason || abortError());
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(signal.reason || abortError());
    signal.addEventListener("abort", onAbort, { once: true });
    Promise.resolve(promise).then(resolve, reject).finally(() => {
      signal.removeEventListener("abort", onAbort);
    });
  });
}

export async function requestJson(path, options = {}) {
  const { timeoutMs, signal, ...fetchOptions } = options;
  const timeout = withTimeout(signal, timeoutMs);
  try {
    const fetchImpl = globalThis.fetch.bind(globalThis);
    const backendUrl = await awaitWithAbort(getBackendBaseUrl(), timeout.signal);
    const sessionToken = await awaitWithAbort(ensurePopupSession({
      fetchImpl,
      signal: timeout.signal,
    }), timeout.signal);
    const requestOptions = { ...fetchOptions };
    if (timeout.signal) requestOptions.signal = timeout.signal;
    const response = await awaitWithAbort(popupAuthenticatedFetch(
      `${backendUrl}${path}`,
      requestOptions,
      fetchImpl,
      { sessionToken, signal: timeout.signal },
    ), timeout.signal);
    if (!response.ok) {
      let details = null;
      try {
        details = await awaitWithAbort(response.json(), timeout.signal);
      } catch (error) {
        if (timeout.signal?.aborted) throw error;
        details = null;
      }
      const error = new Error(`${path} request failed: ${response.status}`);
      error.status = response.status;
      error.details = details;
      throw error;
    }
    return await awaitWithAbort(response.json(), timeout.signal);
  } finally {
    timeout.cleanup();
  }
}

function getChromeStorageLocal() {
  return globalThis.chrome?.storage?.local || null;
}

function storageGet(key) {
  const local = getChromeStorageLocal();
  if (!local?.get) return Promise.resolve(null);
  return new Promise((resolve) => {
    try {
      const maybePromise = local.get(key, (items) => resolve(items || {}));
      if (maybePromise?.then) {
        maybePromise.then((items) => resolve(items || {})).catch(() => resolve(null));
      }
    } catch {
      resolve(null);
    }
  });
}

function storageSet(items) {
  const local = getChromeStorageLocal();
  if (!local?.set) return Promise.resolve(false);
  return new Promise((resolve) => {
    try {
      const maybePromise = local.set(items, () => resolve(true));
      if (maybePromise?.then) {
        maybePromise.then(() => resolve(true)).catch(() => resolve(false));
      }
    } catch {
      resolve(false);
    }
  });
}

export async function cacheConfigSnapshot(config) {
  if (!config || !getChromeStorageLocal()) return null;
  const snapshot = {
    config,
    cached_at: new Date().toISOString(),
  };
  const ok = await storageSet({ [CONFIG_CACHE_KEY]: snapshot });
  return ok ? snapshot : null;
}

export async function readCachedConfigSnapshot() {
  const items = await storageGet(CONFIG_CACHE_KEY);
  const snapshot = items?.[CONFIG_CACHE_KEY];
  if (!snapshot || typeof snapshot !== "object" || !snapshot.config) {
    return null;
  }
  return snapshot;
}

// Liveness probe budget. /api/ping answers in milliseconds when the backend
// is up; anything slower than this means "treat as offline and let the next
// poll/WS event flip the badge back".
const PING_TIMEOUT_MS = 3_000;

export async function checkBackendStatus() {
  const backendUrl = await getBackendBaseUrl();
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), PING_TIMEOUT_MS);
    try {
      const response = await fetch(`${backendUrl}/ping`, { method: "GET", signal: ctrl.signal });
      // Any response from /ping settles liveness — except a 404, which means
      // an older backend without the route; fall through to /health below.
      if (response.status !== 404) return response.ok;
    } finally {
      clearTimeout(timer);
    }
  } catch {
    return false;
  }
  // Older backend without /api/ping: fall back to the full health payload.
  // (/health can stall seconds on a cold embedding probe — that latency is
  // exactly why the badge prefers /ping.)
  return (await fetchHealth()) !== null;
}

// Full /health payload (status, profile_ready, embedding_ready, ...).
// Returns null when the backend is unreachable so callers can no-op
// instead of throwing on startup.
export async function fetchHealth() {
  const backendUrl = await getBackendBaseUrl();
  const now = Date.now();
  const cacheTtlMs =
    healthCachePayload === null ? HEALTH_FAILURE_CACHE_TTL_MS : HEALTH_SUCCESS_CACHE_TTL_MS;
  if (
    healthCacheHasValue &&
    healthCacheBaseUrl === backendUrl &&
    now - healthCacheCheckedAt < cacheTtlMs
  ) {
    return healthCachePayload;
  }
  if (healthProbeInFlight && healthProbeInFlight.baseUrl === backendUrl) {
    return healthProbeInFlight.promise;
  }

  const promise = (async () => {
    try {
      const response = await fetch(`${backendUrl}/health`, { method: "GET" });
      if (!response.ok) return null;
      return await response.json();
    } catch {
      return null;
    }
  })()
    .then((payload) => {
      healthCacheBaseUrl = backendUrl;
      healthCacheCheckedAt = Date.now();
      healthCacheHasValue = true;
      healthCachePayload = payload;
      return payload;
    })
    .finally(() => {
      if (healthProbeInFlight?.promise === promise) {
        healthProbeInFlight = null;
      }
    });
  healthProbeInFlight = { baseUrl: backendUrl, promise };
  return promise;
}

export async function fetchProjectStats() {
  return requestJson("/project-stats", { method: "GET", timeoutMs: 6000 });
}

export function __resetPopupHealthCacheForTests() {
  healthCacheBaseUrl = "";
  healthCacheCheckedAt = 0;
  healthCacheHasValue = false;
  healthCachePayload = null;
  healthProbeInFlight = null;
}

// One-click embedding repair (v0.3.155+): POST asks the backend to
// (re-)pull the configured Ollama embedding model; GET reports progress.
// Returns {status, ...payload} — callers branch on status/error instead of
// throwing, because each 409 flavor gets its own user-facing hint. A 404
// status means an older backend without the route.
export async function startEmbeddingRepair() {
  const backendUrl = await getBackendBaseUrl();
  try {
    const response = await popupAuthenticatedFetch(`${backendUrl}/embedding/repair`, {
      method: "POST",
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    return { status: response.status, ...payload };
  } catch {
    return { status: 0 };
  }
}

// Progress of the in-flight (or last finished) repair; null when unreachable.
export async function fetchEmbeddingRepairStatus() {
  const backendUrl = await getBackendBaseUrl();
  try {
    const response = await popupAuthenticatedFetch(`${backendUrl}/embedding/repair`, {
      method: "GET",
    });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

export async function fetchRecommendations() {
  const payload = await requestJson("/recommendations", { method: "GET" });
  return Array.isArray(payload.items) ? payload.items.map(normalizeRecommendation) : [];
}

/** Merge one opaque-cursor page without appending a canonical item twice. */
export function reconcileContentHistoryPage({
  items = [],
  incomingItems = [],
  incomingTotal = 0,
  nextCursor = "",
  hasMore = false,
  append = false,
} = {}) {
  const current = Array.isArray(items) ? items : [];
  const incoming = Array.isArray(incomingItems) ? incomingItems : [];
  const normalizedTotal = Math.max(0, Number(incomingTotal) || 0);
  const reasons = new Set();
  const seen = new Set();
  const merged = [];

  const addItem = (item) => {
    const itemKey = String(item?.item_key || "").trim();
    if (!itemKey) {
      reasons.add("missing_item_key");
      return;
    }
    if (seen.has(itemKey)) {
      reasons.add("duplicate_item_key");
      return;
    }
    seen.add(itemKey);
    merged.push(item);
  };

  if (append) current.forEach(addItem);
  incoming.forEach(addItem);
  const normalizedNextCursor = hasMore ? String(nextCursor || "").trim() : "";

  return {
    items: merged,
    total: normalizedTotal,
    nextCursor: normalizedNextCursor,
    hasMore: Boolean(hasMore && normalizedNextCursor),
    reasons: [...reasons],
  };
}

export async function fetchContentHistory(category, limit = 12, cursorOrOffset = "") {
  if (!["clicked", "shown", "removed"].includes(category)) {
    throw new TypeError(`Unknown content history category: ${category}`);
  }
  const params = new URLSearchParams({
    category,
    limit: String(Math.max(1, Math.min(50, Math.floor(Number(limit) || 12)))),
  });
  // Cursor pagination is the current contract. Keep non-zero numeric offsets
  // for older popup callers during migration, but never send cursor="": the
  // backend rejects an empty opaque cursor instead of treating it as page one.
  if (typeof cursorOrOffset === "number") {
    const offset = Math.max(0, Math.floor(Number(cursorOrOffset) || 0));
    if (offset > 0) params.set("offset", String(offset));
  } else {
    const cursor = String(cursorOrOffset || "").trim();
    if (cursor) params.set("cursor", cursor);
  }
  const payload = await requestJson(`/content-history?${params}`, {
    method: "GET",
    timeoutMs: CONTENT_HISTORY_READ_TIMEOUT_MS,
  });
  return {
    ...payload,
    items: Array.isArray(payload?.items) ? payload.items : [],
    total: Math.max(0, Number(payload?.total) || 0),
    has_more: payload?.has_more === true,
    next_cursor: payload?.has_more === true ? String(payload?.next_cursor || "") : "",
  };
}

export async function refreshRecommendations() {
  return requestJson("/recommendations/refresh", { method: "POST" });
}

export async function reshuffleRecommendations(excludedBvids = []) {
  const payload = await requestJson("/recommendations/reshuffle", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ excluded_bvids: excludedBvids }),
  });
  return {
    ...payload,
    items: Array.isArray(payload.items) ? payload.items.map(normalizeRecommendation) : [],
  };
}

export async function appendRecommendations(excludedBvids = []) {
  const payload = await requestJson("/recommendations/append", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ excluded_bvids: excludedBvids }),
  });
  return {
    ...payload,
    items: Array.isArray(payload.items) ? payload.items.map(normalizeRecommendation) : [],
  };
}

export async function fetchRuntimeStatus() {
  return requestJson("/runtime-status", { method: "GET" });
}

export async function fetchDiagnosticsAlerts({ limit = 50 } = {}) {
  const boundedLimit = Number.isFinite(limit) ? Math.max(1, Math.min(Math.trunc(limit), 500)) : 50;
  return requestJson(`/diagnostics/alerts?limit=${boundedLimit}`, {
    method: "GET",
    timeoutMs: 12_000,
  });
}

export async function fetchInitStatus() {
  return requestJson("/init-status", { method: "GET", timeoutMs: 45000 });
}

export async function fetchXSourceStatus() {
  return requestJson("/sources/x/status", { method: "GET" });
}

export async function fetchSourcesStatus() {
  return requestJson("/sources/status", { method: "GET" });
}

export async function fetchV2exIdentity() {
  return requestJson("/sources/v2ex/identity", { method: "GET", timeoutMs: 12_000 });
}

export async function acceptV2exBrowserIdentity(username) {
  return requestJson("/sources/v2ex/identity", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: String(username || "").trim(), accept: true }),
    timeoutMs: 12_000,
  });
}

// The counterpart to fetchSourcesStatus: that one is polled and never goes out,
// this one is an explicit user action and is the only place a platform gets
// probed. Generous timeout because it really can reach the network — a B站 nav
// probe or a 5s wait for this very extension to answer a heartbeat request.
export async function verifySource(slug) {
  return requestJson(`/sources/${encodeURIComponent(slug)}/verify`, {
    method: "POST",
    timeoutMs: 30_000,
  });
}

export async function startInit({
  force = false,
  sources,
  bangumiUsername = null,
  bangumiToken = null,
  githubUsername = null,
  githubToken = null,
  llmConcurrency = null,
  initTimeoutMinutes = null,
  // Accept snake_case keys for callers that build a raw request payload.
  llm_concurrency: llmConcurrencyLegacy = null,
  init_timeout_minutes: initTimeoutMinutesLegacy = null,
} = {}) {
  const payload = { force };
  const effectiveLlmConcurrency = llmConcurrency ?? llmConcurrencyLegacy;
  if (Number.isFinite(Number(effectiveLlmConcurrency)) && Number(effectiveLlmConcurrency) >= 1) {
    payload.llm_concurrency = Number(effectiveLlmConcurrency);
  }
  const effectiveInitTimeoutMinutes = initTimeoutMinutes ?? initTimeoutMinutesLegacy;
  if (Number.isFinite(Number(effectiveInitTimeoutMinutes)) && Number(effectiveInitTimeoutMinutes) >= 1 && Number(effectiveInitTimeoutMinutes) <= 1440) {
    payload.init_timeout_minutes = Number(effectiveInitTimeoutMinutes);
  }
  // Only attach an explicit per-run platform selection when given; omitting it
  // lets the backend fall back to all config-enabled sources (legacy behaviour).
  if (Array.isArray(sources)) {
    payload.sources = sources;
  }
  // Send source-scoped options only when the caller has one to send.
  // `null`/`undefined` means "leave the configured value untouched" (the backend
  // treats an omitted field as keep-existing); an empty username is a deliberate
  // clear. Tokens remain write-only and are only sent when explicitly typed.
  const sourceOptions = {};
  if (Array.isArray(sources) && sources.includes("bangumi")) {
    const bangumi = {};
    if (bangumiUsername != null) {
      bangumi.username = String(bangumiUsername).trim();
    }
    if (bangumiToken != null) {
      bangumi.access_token = String(bangumiToken).trim();
    }
    if (Object.keys(bangumi).length > 0) {
      sourceOptions.bangumi = bangumi;
    }
  }
  if (Array.isArray(sources) && sources.includes("github")) {
    const github = {};
    if (githubUsername != null) {
      github.username = String(githubUsername).trim();
    }
    if (githubToken != null) {
      github.access_token = String(githubToken).trim();
    }
    if (Object.keys(github).length > 0) {
      sourceOptions.github = github;
    }
  }
  if (Object.keys(sourceOptions).length > 0) payload.source_options = sourceOptions;
  return requestJson("/init", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 60000,
  });
}

export async function cancelInit() {
  return requestJson("/init/cancel", { method: "POST", timeoutMs: 15000 });
}

export async function fetchUpdateStatus() {
  return requestJson("/update-status", { method: "GET" });
}

export async function checkBackendUpdate() {
  return requestJson("/update/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ include_backend: true }),
  });
}

export async function applyBackendUpdate(tag = "") {
  return requestJson("/update/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target: "backend", tag }),
  });
}

export async function fetchActivityFeed({ limit, before } = {}) {
  const params = new URLSearchParams();
  if (typeof limit === "number") params.set("limit", String(limit));
  if (before) params.set("before", before);
  const qs = params.toString();
  return requestJson(`/activity-feed${qs ? `?${qs}` : ""}`, { method: "GET" });
}

export async function fetchPendingNotification() {
  return requestJson("/notifications/pending", { method: "GET" });
}

export async function fetchPendingDelight() {
  const payload = await requestJson("/delight/pending", { method: "GET" });
  return payload?.item ?? null;
}

export async function fetchPendingDelightBatch(limit = null) {
  const params = new URLSearchParams();
  if (typeof limit === "number" && Number.isFinite(limit)) {
    params.set("limit", String(Math.max(1, Math.min(100, Math.floor(limit)))));
  }
  const qs = params.toString();
  const payload = await requestJson(
    `/delight/pending-batch${qs ? `?${qs}` : ""}`,
    { method: "GET" },
  );
  return Array.isArray(payload?.items) ? payload.items : [];
}

export async function markDelightSent(bvid) {
  return requestJson("/delight/sent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bvid }),
  });
}

export async function acknowledgeNotificationSent(bvid) {
  return requestJson("/notifications/sent", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ bvid }),
  });
}

export async function fetchProfileSummary({ limit, cursor } = {}) {
  const params = new URLSearchParams();
  if (typeof limit === "number" && Number.isFinite(limit)) {
    params.set("limit", String(limit));
  }
  if (typeof cursor === "string" && cursor.trim()) {
    params.set("cursor", cursor.trim());
  }
  const query = params.toString();
  return requestJson(`/profile-summary${query ? `?${query}` : ""}`, { method: "GET" });
}

export async function fetchEditState() {
  return requestJson("/profile/edit-state", { method: "GET" });
}

const PENDING_REQUEST_IDS_KEY = "obc_pending_request_ids";
const pendingRequestIds = new Map();
let pendingRequestIdsReady = null;

function newRequestId() {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

async function loadPendingRequestIds() {
  if (pendingRequestIdsReady) return pendingRequestIdsReady;
  pendingRequestIdsReady = (async () => {
    const items = await storageGet(PENDING_REQUEST_IDS_KEY);
    const stored = items?.[PENDING_REQUEST_IDS_KEY];
    if (!stored || typeof stored !== "object" || Array.isArray(stored)) return;
    Object.entries(stored).forEach(([key, value]) => {
      if (typeof value === "string" && value) pendingRequestIds.set(key, value);
    });
  })();
  return pendingRequestIdsReady;
}

async function persistPendingRequestIds() {
  await storageSet({ [PENDING_REQUEST_IDS_KEY]: Object.fromEntries(pendingRequestIds) });
}

async function rememberPendingId(namespace, key) {
  await loadPendingRequestIds();
  const storageKey = `${namespace}:${key}`;
  const existing = pendingRequestIds.get(storageKey);
  if (existing) return existing;
  const requestId = newRequestId();
  pendingRequestIds.set(storageKey, requestId);
  if (pendingRequestIds.size > 200) pendingRequestIds.delete(pendingRequestIds.keys().next().value);
  await persistPendingRequestIds();
  return requestId;
}

async function forgetPendingId(namespace, key) {
  await loadPendingRequestIds();
  pendingRequestIds.delete(`${namespace}:${key}`);
  await persistPendingRequestIds();
}

function feedbackRequestKey(payload) {
  return [payload.recommendation_id, payload.feedback_type, payload.note || ""].join("|");
}

export async function submitProfileEdit({ target, op, value = null, parent = "", weight = null }) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 35_000);
  try {
    return await requestJson("/profile/edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target, op, value, parent, weight }),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
}

export async function submitFeedback(payload) {
  const key = feedbackRequestKey(payload);
  const request_id = payload.request_id || await rememberPendingId("feedback", key);
  try {
    const response = await requestJson("/feedback", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ...payload, request_id }),
    });
    await forgetPendingId("feedback", key);
    return response;
  } catch (error) {
    throw error;
  }
}

export async function sendBehaviorEvents(events, { retryKey = "" } = {}) {
  // Identity belongs to the concrete event instance, never to a similarity
  // key. Mutating the caller-owned object preserves it when the same network
  // request is retried, while two identical-looking actions remain distinct.
  if (retryKey && events.length === 1 && !String(events[0]?.event_id || "").trim()) {
    events[0].event_id = await rememberPendingId("behavior-command", retryKey);
  }
  events.forEach((event) => {
    const existing = String(event?.event_id || "").trim();
    event.event_id = existing || newRequestId();
  });
  const response = await requestJson("/events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ events }),
  });
  if (retryKey && Number(response?.accepted || 0) >= 1) {
    await forgetPendingId("behavior-command", retryKey);
  }
  return response;
}

/**
 * Report a click-through on a recommendation card. Best-effort: errors are
 * swallowed so UI navigation is never blocked by a slow or offline backend.
 *
 * @param {{
 *   bvid: string,
 *   content_id?: string,
 *   content_url?: string,
 *   source_platform?: string,
 *   title?: string,
 *   recommendation_id?: number | null,
 *   topic_label?: string,
 *   up_name?: string,
 * }} payload
 * @returns {Promise<boolean>} true if the click was reported successfully
 */
export async function reportRecommendationClick(payload) {
  const stableRecommendationId = payload.recommendation_id || "";
  const stableContentId = String(payload.content_id || payload.bvid || "").trim();
  let fallbackUrl = "";
  if (!stableRecommendationId && !stableContentId) {
    const rawUrl = String(payload.content_url || "").trim();
    try {
      const normalizedUrl = new URL(rawUrl);
      normalizedUrl.hash = "";
      fallbackUrl = normalizedUrl.toString();
    } catch {
      fallbackUrl = rawUrl;
    }
  }
  const key = [
    stableRecommendationId,
    stableContentId || fallbackUrl,
  ].join("|");
  const request_id = payload.request_id || await rememberPendingId("recommendation-click", key);
  try {
    await requestJson("/recommendation-click", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ...payload,
        request_id,
      }),
    });
    await forgetPendingId("recommendation-click", key);
    return true;
  } catch (error) {
    // Best-effort reporting — do not disrupt the user's click.
    return false;
  }
}

export async function sendChatMessage(message) {
  const controller = new AbortController();
  // Bumped from 35s to 150s. Backend's chat dialogue can take ~120s under
  // deepseek reasoning_effort=max; we give a small headroom for HTTP
  // round-trip + serialization beyond the backend's own 120s wait_for.
  const timeout = setTimeout(() => controller.abort(), 150_000);
  try {
    return await requestJson("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
}

export async function startChatTurn({
  turnId = "",
  session = "popup",
  scope = "chat",
  subjectId = "",
  subjectTitle = "",
  replyToTurnId = "",
  sessionId = "",
  skill = "",
  streaming = false,
  message,
}) {
  const payload = {
    turn_id: turnId,
    session,
    scope,
    subject_id: subjectId,
    subject_title: subjectTitle,
    message,
    streaming,
  };
  if (replyToTurnId) payload.reply_to_turn_id = replyToTurnId;
  if (sessionId) payload.session_id = sessionId;
  if (skill) payload.skill = skill;
  return requestJson("/chat/turns", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function streamChatTurn({
  turnId = "",
  message = "",
  session = "popup",
  scope = "chat",
  subjectId = "",
  subjectTitle = "",
  replyToTurnId = "",
  onPhase,
  onToolCall,
  onContent,
  onDone,
  watchdogMs,
} = {}) {
  const backendUrl = await getBackendBaseUrl();
  const watchdog = agentChatShared().createSseReadWatchdog(
    watchdogMs > 0 ? { timeoutMs: watchdogMs } : undefined,
  );
  const response = await globalThis.fetch(`${backendUrl}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      turn_id: turnId,
      session,
      scope,
      subject_id: subjectId,
      subject_title: subjectTitle,
      reply_to_turn_id: replyToTurnId,
      message,
    }),
    signal: watchdog.signal,
  });
  if (!response.ok) {
    watchdog.cancel();
    throw new Error(`chat stream failed: ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "";
  let currentData = "";
  const dispatch = () => {
    if (!currentEvent || !currentData) return;
    try {
      const data = JSON.parse(currentData);
      if (currentEvent === "content" && typeof onContent === "function") {
        onContent(String(data.delta || ""));
      } else if (currentEvent === "tool_call" && typeof onToolCall === "function") {
        onToolCall(data);
      } else if (currentEvent === "done" && typeof onDone === "function") {
        onDone(data);
      } else if (currentEvent === "phase" && typeof onPhase === "function") {
        onPhase(data);
      }
    } catch {
      // Ignore malformed SSE lines; keep the stream alive.
    }
    currentEvent = "";
    currentData = "";
  };
  try {
    while (true) {
      // 每轮读前重置看门狗：服务端心跳注释行也算字节，会喂活它。
      watchdog.reset();
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() || "";
      for (const raw of lines) {
        const line = raw.trim();
        if (line.startsWith("event:")) {
          currentEvent = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          currentData = line.slice(5).trim();
          dispatch();
        }
      }
    }
  } finally {
    watchdog.cancel();
  }
}

export async function fetchChatTurn(turnId, { signal, timeoutMs = 10_000 } = {}) {
  return requestJson(`/chat/turns/${encodeURIComponent(turnId)}`, {
    method: "GET",
    signal,
    timeoutMs,
  });
}

export async function fetchChatContext(turnId, { signal, timeoutMs = 5_000 } = {}) {
  return requestJson(`/chat/contexts/${encodeURIComponent(turnId)}`, {
    method: "GET",
    signal,
    timeoutMs,
  });
}

export async function fetchChatTurns({ session = "popup", scope = "", limit = 50 } = {}) {
  const params = new URLSearchParams();
  params.set("session", session);
  if (scope) params.set("scope", scope);
  if (typeof limit === "number" && Number.isFinite(limit)) {
    params.set("limit", String(Math.max(1, Math.floor(limit))));
  }
  return requestJson(`/chat/turns?${params.toString()}`, { method: "GET" });
}

export async function fetchPendingConfirmations({
  countOnly = false,
  session = "",
} = {}) {
  const params = new URLSearchParams();
  if (countOnly) params.set("count_only", "1");
  if (session) params.set("session", session);
  const suffix = params.size ? `?${params.toString()}` : "";
  return requestJson(`/chat/pending-confirmations${suffix}`, { method: "GET" });
}

export async function openPendingConfirmation(ref, { session = "popup", signal } = {}) {
  return requestJson(`/chat/pending-confirmations/${encodeURIComponent(String(ref || ""))}/open`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session }),
    signal,
  });
}

export async function actOnChatCard(turnId, action, { signal } = {}) {
  return requestJson(`/chat/cards/${encodeURIComponent(String(turnId || ""))}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
    signal,
    timeoutMs: 60_000,
  });
}

// ── Chat agent loop (「聊一聊」 M9) ─────────────────────────────
// SSE frame parsing and the process-flow run model are shared with the
// mobile/desktop web via popup/shared/agent-chat.js (copied from
// src/openbiliclaw/web/shared at build time).
function agentChatShared() {
  const shared = globalThis.OpenBiliClawAgentChat;
  if (!shared) throw new Error("agent-chat shared helper did not load");
  return shared;
}

async function postAuthenticatedSse(path, body, { signal } = {}) {
  const fetchImpl = globalThis.fetch.bind(globalThis);
  const backendUrl = await getBackendBaseUrl();
  const sessionToken = await ensurePopupSession({ fetchImpl });
  const response = await popupAuthenticatedFetch(
    `${backendUrl}${path}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    },
    fetchImpl,
    { sessionToken },
  );
  if (!response.ok) {
    let details = null;
    try { details = await response.json(); } catch { details = null; }
    const error = new Error(`${path} request failed: ${response.status}`);
    error.status = response.status;
    error.details = details;
    throw error;
  }
  return response;
}

async function readAgentSseStream(response, onEvent, watchdog) {
  const parser = agentChatShared().createAgentSseParser(onEvent);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  try {
    while (true) {
      // Rearm on every read: any incoming byte (incl. server ``: ping``
      // heartbeat comments) proves the connection is still alive.
      watchdog?.reset();
      const { done, value } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
  } finally {
    watchdog?.cancel();
  }
  parser.end();
}

/**
 * Stream one multi-hop agent turn (POST /api/chat/agent/stream).
 * ``onEvent(eventName, data)`` receives every AgentEvent; resolves with the
 * terminal ``done`` payload; EOF without it rejects as an interrupted stream.
 * Throws with ``status === 503`` when the
 * loop is disabled so callers can fall back to the legacy stream; an SSE
 * ``error`` frame rejects with ``agentStreamError = true``. A read watchdog
 * aborts the request when no byte arrives within the watchdog window.
 */
export async function streamAgentChatTurn({
  turnId = "",
  sessionId = "",
  skill = "",
  session = "popup",
  message,
  onEvent,
  watchdogMs,
} = {}) {
  const body = { message };
  if (turnId) body.turn_id = turnId;
  if (sessionId) body.session_id = sessionId;
  if (skill) body.skill = skill;
  if (session) body.session = session;
  let donePayload = null;
  const watchdog = agentChatShared().createSseReadWatchdog(
    watchdogMs > 0 ? { timeoutMs: watchdogMs } : undefined,
  );
  const response = await postAuthenticatedSse("/chat/agent/stream", body, { signal: watchdog.signal });
  await readAgentSseStream(response, (name, data) => {
    if (name === "done") donePayload = data;
    if (name === "error") {
      const error = new Error(String(data?.error || "对话失败了，请稍后重试。"));
      error.agentStreamError = true;
      onEvent?.(name, data);
      throw error;
    }
    onEvent?.(name, data);
  }, watchdog);
  if (!donePayload) throw new Error("对话连接已中断，等待历史恢复。");
  return donePayload;
}

export async function fetchChatSkills() {
  const data = await requestJson("/chat/skills", { timeoutMs: 5_000 });
  return agentChatShared().normalizeChatSkillList(data);
}

export async function fetchChatPersonas() {
  const data = await requestJson("/chat/personas", { timeoutMs: 5_000 });
  return {
    personas: Array.isArray(data?.personas) ? data.personas : [],
    examplePrompt: String(data?.example_prompt || ""),
  };
}

export async function fetchChatSessions({ includeArchived = false, limit = 100 } = {}) {
  const params = new URLSearchParams({ limit: String(Math.max(1, Math.min(500, limit))) });
  if (includeArchived) params.set("include_archived", "true");
  const data = await requestJson(`/chat/sessions?${params.toString()}`);
  return Array.isArray(data?.items) ? data.items : [];
}

export async function createChatSession({ title = "", sessionId = "" } = {}) {
  const body = {};
  if (sessionId) body.session_id = sessionId;
  if (title) body.title = title;
  return requestJson("/chat/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateChatSession(sessionId, { title, archived, persona } = {}) {
  const body = {};
  if (typeof title === "string") body.title = title;
  if (typeof archived === "boolean") body.archived = archived;
  if (typeof persona === "string") body.persona = persona;
  return requestJson(`/chat/sessions/${encodeURIComponent(String(sessionId || ""))}`, {
    method: "PATCH",
    timeoutMs: 10_000,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchChatSessionDetail(sessionId, { limit = 100, offset = 0 } = {}) {
  const params = new URLSearchParams({
    limit: String(Math.max(1, Math.min(200, limit))),
    offset: String(Math.max(0, offset)),
  });
  return requestJson(
    `/chat/sessions/${encodeURIComponent(String(sessionId || "default"))}?${params.toString()}`,
  );
}

export async function fetchChatApprovals({ status = "pending", limit = 50 } = {}) {
  const params = new URLSearchParams({ limit: String(Math.max(1, Math.min(200, limit))) });
  if (status) params.set("status", status);
  const data = await requestJson(`/chat/approvals?${params.toString()}`, { timeoutMs: 5_000 });
  return agentChatShared().normalizeApprovalList(data);
}

export async function approveChatApproval(approvalId) {
  return requestJson(
    `/chat/approvals/${encodeURIComponent(String(approvalId || ""))}/approve`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
  );
}

export async function rejectChatApproval(approvalId, reason = "") {
  return requestJson(
    `/chat/approvals/${encodeURIComponent(String(approvalId || ""))}/reject`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    },
  );
}

export async function createAgentTask({ prompt, sessionId = "", title = "", skill = "" } = {}) {
  const body = { prompt: String(prompt || "") };
  if (sessionId) body.session_id = sessionId;
  if (title) body.title = title;
  if (skill) body.skill = skill;
  return requestJson("/chat/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchAgentTasks({ status = "", sessionId = "", limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams({
    limit: String(Math.max(1, Math.min(200, limit))),
    offset: String(Math.max(0, offset)),
  });
  if (status) params.set("status", status);
  if (sessionId) params.set("session_id", sessionId);
  const data = await requestJson(`/chat/tasks?${params.toString()}`);
  const items = Array.isArray(data?.items) ? data.items : [];
  return {
    items: items.map((item) => agentChatShared().normalizeAgentTask(item)).filter(Boolean),
    total: Math.max(0, Number(data?.total) || 0),
  };
}

export async function fetchAgentTask(taskId) {
  const data = await requestJson(`/chat/tasks/${encodeURIComponent(String(taskId || ""))}`);
  return agentChatShared().normalizeAgentTask(data);
}

export async function cancelAgentTask(taskId) {
  const data = await requestJson(
    `/chat/tasks/${encodeURIComponent(String(taskId || ""))}/cancel`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
  );
  return agentChatShared().normalizeAgentTask(data);
}

export async function respondToInterestProbe(domain, responseType, message = "") {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 35_000);
  try {
    return await requestJson("/interest-probes/respond", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, response: responseType, message }),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
}

export async function respondToAvoidanceProbe(domain, responseType, message = "") {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 35_000);
  try {
    return await requestJson("/avoidance-probes/respond", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, response: responseType, message }),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
}

export async function respondToDelight(bvid, responseType, title = "", message = "") {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 35_000);
  const durableReaction = ["like", "dislike", "dismiss"].includes(responseType);
  const key = `${bvid}|${responseType}`;
  const request_id = durableReaction
    ? await rememberPendingId("delight-response", key)
    : "";
  try {
    const response = await requestJson("/delight/respond", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bvid, response: responseType, title, message, request_id }),
      signal: controller.signal,
    });
    if (durableReaction) await forgetPendingId("delight-response", key);
    return response;
  } finally {
    clearTimeout(timeout);
  }
}

export async function fetchConfig(timeoutMs = CONFIG_GET_TIMEOUT_MS) {
  const config = await requestJson("/config", { method: "GET", timeoutMs });
  await cacheConfigSnapshot(config);
  return config;
}

export async function fetchSourceShareSuggestion(overrides = null) {
  if (overrides && typeof overrides === "object") {
    return requestJson("/config/source-share-suggestion", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(overrides),
    });
  }
  return requestJson("/config/source-share-suggestion", { method: "GET" });
}

export async function probeConfigService(kind, config, instanceId = "") {
  return requestJson("/config/probe-service", {
    method: "POST",
    // Keep the popup alive beyond the backend's bounded 120s LLM cold-start
    // probe window; otherwise the browser aborts a request the backend still
    // legitimately owns.
    timeoutMs: 125_000,
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      kind,
      config,
      ...(instanceId ? { instance_id: instanceId } : {}),
    }),
  });
}

export async function discoverConfigModels(config, instanceId) {
  return requestJson("/config/discover-models", {
    method: "POST",
    timeoutMs: 25_000,
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      instance_id: String(instanceId || ""),
      config,
    }),
  });
}

export async function updateConfig(data, timeoutMs = CONFIG_PUT_TIMEOUT_MS) {
  return requestJson("/config", {
    method: "PUT",
    timeoutMs,
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(data),
  });
}

export async function updateRuntimeToggle(name, value) {
  const enabled = Boolean(value);
  if (name === "pause_llm") {
    return updateConfig({ scheduler: { enabled: !enabled } });
  }
  if (name === "pause_on_disconnect") {
    return updateConfig({ scheduler: { pause_on_extension_disconnect: enabled } });
  }
  throw new Error(`Unknown runtime toggle: ${name}`);
}

// ── Watch-later ──────────────────────────────────────────────────

// Saved-item mutations are tiny local-DB writes server-side, but the popup
// fires dozens of cover/status requests at the same origin on open — Chrome's
// 6-connections-per-origin limit can queue a DELETE behind slow image-proxy
// fetches. A bounded timeout turns "hangs forever, button stuck disabled"
// into a visible, retryable failure.
const SAVED_MUTATION_TIMEOUT_MS = 10_000;
const SAVED_READ_TIMEOUT_MS = 10_000;

function savedListPath(listKind) {
  if (listKind !== "favorite" && listKind !== "watch_later") {
    throw new TypeError(`Unknown saved list: ${listKind}`);
  }
  return `/saved/${listKind}`;
}

/** Keep platform routing on the backend; clients only normalize identity fields. */
export function normalizeSavedItemInput(item = {}) {
  const sourcePlatform = String(item.source_platform || item.platform || "bilibili").trim();
  const legacyId = String(item.bvid || "").trim();
  const contentId = String(
    item.content_id || (legacyId && !legacyId.includes(":") ? legacyId : ""),
  ).trim();
  return {
    source_platform: sourcePlatform,
    content_id: contentId,
    content_url: String(item.content_url || item.url || "").trim(),
    content_type: String(
      item.content_type || (sourcePlatform === "bilibili" && contentId ? "video" : ""),
    ).trim(),
    title: String(item.title || "").trim(),
    author_name: String(item.author_name || item.up_name || item.author || "").trim(),
    cover_url: String(item.cover_url || "").trim(),
    note: String(item.note || "").trim(),
  };
}

export async function saveItem(listKind, item, timeoutMs = SAVED_MUTATION_TIMEOUT_MS) {
  return requestJson(savedListPath(listKind), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(normalizeSavedItemInput(item)),
    timeoutMs,
  });
}

export async function removeSavedItem(listKind, itemKey, timeoutMs = SAVED_MUTATION_TIMEOUT_MS) {
  return requestJson(`${savedListPath(listKind)}/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_key: String(itemKey || "").trim() }),
    timeoutMs,
  });
}

export async function fetchSavedItems(listKind, limit = 50, offset = 0, timeoutMs = SAVED_READ_TIMEOUT_MS) {
  const payload = await requestJson(
    `${savedListPath(listKind)}?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
    { timeoutMs },
  );
  return {
    ...payload,
    items: Array.isArray(payload?.items) ? payload.items.map(normalizeSavedItem) : [],
  };
}

export async function savedItemStatus(listKind, itemKey, timeoutMs = SAVED_READ_TIMEOUT_MS) {
  const query = new URLSearchParams({ item_key: String(itemKey || "").trim() });
  return requestJson(`${savedListPath(listKind)}/status?${query}`, { timeoutMs });
}

export async function syncSavedItems(listKind, itemKeys = [], timeoutMs = SAVED_MUTATION_TIMEOUT_MS) {
  return requestJson(`${savedListPath(listKind)}/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_keys: Array.from(new Set(itemKeys.map((key) => String(key || "").trim()).filter(Boolean))),
    }),
    timeoutMs,
  });
}

export async function pollSavedSyncTask(taskId, timeoutMs = SAVED_READ_TIMEOUT_MS) {
  return requestJson(`/saved-sync/tasks/${encodeURIComponent(String(taskId || "").trim())}`, {
    timeoutMs,
  });
}

export async function addToWatchLater(bvid) {
  return requestJson("/watch-later", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bvid }),
    timeoutMs: SAVED_MUTATION_TIMEOUT_MS,
  });
}

export async function removeFromWatchLater(bvid) {
  return requestJson(`/watch-later/${encodeURIComponent(bvid)}`, {
    method: "DELETE",
    timeoutMs: SAVED_MUTATION_TIMEOUT_MS,
  });
}

export async function watchLaterStatus(bvid) {
  return requestJson(`/watch-later/${encodeURIComponent(bvid)}`);
}

export async function fetchWatchLater(limit = 50, offset = 0) {
  const payload = await requestJson(`/watch-later?limit=${limit}&offset=${offset}`);
  return {
    ...payload,
    items: Array.isArray(payload?.items) ? payload.items.map(normalizeSavedItem) : [],
  };
}

// ── Favorites (收藏夹) ────────────────────────────────────────────

export async function addToFavorite(bvid) {
  return requestJson("/favorites", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bvid }),
    timeoutMs: SAVED_MUTATION_TIMEOUT_MS,
  });
}

export async function removeFromFavorite(bvid) {
  return requestJson(`/favorites/${encodeURIComponent(bvid)}`, {
    method: "DELETE",
    timeoutMs: SAVED_MUTATION_TIMEOUT_MS,
  });
}

export async function favoriteStatus(bvid) {
  return requestJson(`/favorites/${encodeURIComponent(bvid)}`);
}

export async function fetchFavorites(limit = 50, offset = 0) {
  const payload = await requestJson(`/favorites?limit=${limit}&offset=${offset}`);
  return {
    ...payload,
    items: Array.isArray(payload?.items) ? payload.items.map(normalizeSavedItem) : [],
  };
}
