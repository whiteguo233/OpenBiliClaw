import { normalizeRecommendation } from "./popup-helpers.js";
import { getBackendBaseUrl } from "./popup-backend-config.js";

export const CONFIG_CACHE_KEY = "openbiliclaw.config_cache";
export const CONFIG_PUT_TIMEOUT_MS = 60_000;

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

export async function requestJson(path, options = {}) {
  const backendUrl = await getBackendBaseUrl();
  const { timeoutMs, signal, ...fetchOptions } = options;
  const timeout = withTimeout(signal, timeoutMs);
  const requestOptions = { ...fetchOptions };
  if (timeout.signal) {
    requestOptions.signal = timeout.signal;
  }
  try {
    const response = await fetch(`${backendUrl}${path}`, requestOptions);
    if (!response.ok) {
      let details = null;
      try {
        details = await response.json();
      } catch {
        details = null;
      }
      const error = new Error(`${path} request failed: ${response.status}`);
      error.status = response.status;
      error.details = details;
      throw error;
    }
    return response.json();
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

export async function checkBackendStatus() {
  try {
    const backendUrl = await getBackendBaseUrl();
    const response = await fetch(`${backendUrl}/health`, { method: "GET" });
    return response.ok;
  } catch {
    return false;
  }
}

export async function fetchRecommendations() {
  const payload = await requestJson("/recommendations", { method: "GET" });
  return Array.isArray(payload.items) ? payload.items.map(normalizeRecommendation) : [];
}

export async function refreshRecommendations() {
  return requestJson("/recommendations/refresh", { method: "POST" });
}

export async function reshuffleRecommendations() {
  const payload = await requestJson("/recommendations/reshuffle", { method: "POST" });
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

export async function fetchPendingDelightBatch(limit = 20) {
  const payload = await requestJson(
    `/delight/pending-batch?limit=${limit}`,
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

export async function submitFeedback(payload) {
  return requestJson("/feedback", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
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
  try {
    await requestJson("/recommendation-click", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
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
  message,
}) {
  return requestJson("/chat/turns", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      turn_id: turnId,
      session,
      scope,
      subject_id: subjectId,
      subject_title: subjectTitle,
      message,
    }),
  });
}

export async function fetchChatTurn(turnId) {
  return requestJson(`/chat/turns/${encodeURIComponent(turnId)}`, { method: "GET" });
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
  try {
    return await requestJson("/delight/respond", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bvid, response: responseType, title, message }),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
}

export async function fetchConfig() {
  const config = await requestJson("/config?reveal_keys=true", { method: "GET" });
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

export async function updateConfig(data) {
  return requestJson("/config", {
    method: "PUT",
    timeoutMs: CONFIG_PUT_TIMEOUT_MS,
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
