/**
 * Backend API client for mobile web.
 * Mirrors extension popup-api.js but without Chrome-specific code.
 */

const BASE_URL = `${location.protocol}//${location.host}/api`;

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
  if (!hasTimeout && !signal) return { signal: undefined, cleanup() {} };
  if (!hasTimeout) return { signal, cleanup() {} };

  const controller = new AbortController();
  let tid = null;
  const abort = (reason) => { if (!controller.signal.aborted) controller.abort(reason || abortError()); };
  const onCaller = () => abort(signal?.reason);

  if (signal?.aborted) abort(signal.reason);
  else if (signal) signal.addEventListener("abort", onCaller, { once: true });
  tid = setTimeout(() => abort(abortError("Request timed out")), timeoutMs);

  return {
    signal: controller.signal,
    cleanup() {
      if (tid !== null) clearTimeout(tid);
      if (signal) signal.removeEventListener("abort", onCaller);
    },
  };
}

export async function requestJson(path, options = {}) {
  const { timeoutMs, signal, ...fetchOptions } = options;
  const timeout = withTimeout(signal, timeoutMs);
  if (timeout.signal) fetchOptions.signal = timeout.signal;
  try {
    const res = await fetch(`${BASE_URL}${path}`, fetchOptions);
    if (!res.ok) {
      let details = null;
      try { details = await res.json(); } catch { details = null; }
      const err = new Error(`${path} failed: ${res.status}`);
      err.status = res.status;
      err.details = details;
      throw err;
    }
    return res.json();
  } finally {
    timeout.cleanup();
  }
}

const json = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

// ── Health ──────────────────────────────────────────────────
export async function fetchHealth() {
  return requestJson("/health");
}

export async function checkHealth() {
  try {
    const res = await fetch(`${BASE_URL}/health`, { method: "GET" });
    return res.ok;
  } catch { return false; }
}

// ── Recommendations ─────────────────────────────────────────
export async function fetchRecommendations() {
  const data = await requestJson("/recommendations");
  return Array.isArray(data.items) ? data.items : [];
}

export async function reshuffleRecommendations() {
  const data = await requestJson("/recommendations/reshuffle", { method: "POST" });
  return { ...data, items: Array.isArray(data.items) ? data.items : [] };
}

export async function appendRecommendations(excludedBvids = []) {
  const data = await requestJson("/recommendations/append", json({ excluded_bvids: excludedBvids }));
  return { ...data, items: Array.isArray(data.items) ? data.items : [] };
}

export async function reportClick(payload) {
  try {
    await requestJson("/recommendation-click", json(payload));
    return true;
  } catch { return false; }
}

// ── Runtime Status ──────────────────────────────────────────
export async function fetchRuntimeStatus() {
  return requestJson("/runtime-status");
}

// ── Delight ─────────────────────────────────────────────────
export async function fetchDelightBatch(limit = 20) {
  const data = await requestJson(`/delight/pending-batch?limit=${limit}`);
  return Array.isArray(data?.items) ? data.items : [];
}

export async function respondToDelight(bvid, responseType, title = "", message = "") {
  return requestJson("/delight/respond", {
    ...json({ bvid, response: responseType, title, message }),
    timeoutMs: 35_000,
  });
}

// ── Profile ─────────────────────────────────────────────────
export async function fetchProfileSummary({ limit, cursor } = {}) {
  const params = new URLSearchParams();
  if (typeof limit === "number") params.set("limit", String(limit));
  if (typeof cursor === "string" && cursor.trim()) params.set("cursor", cursor.trim());
  const qs = params.toString();
  return requestJson(`/profile-summary${qs ? `?${qs}` : ""}`);
}

// ── Notifications ───────────────────────────────────────────
export async function fetchPendingNotifications() {
  return requestJson("/notifications/pending");
}

export async function ackNotification(bvid) {
  return requestJson("/notifications/sent", json({ bvid }));
}

// ── Cognition Updates ───────────────────────────────────────
export async function fetchPendingCognitionUpdates() {
  return requestJson("/cognition-updates/pending");
}

export async function markCognitionSeen(id) {
  return requestJson(`/cognition-updates/${encodeURIComponent(id)}/seen`, { method: "POST" });
}

// ── Activity Feed ───────────────────────────────────────────
export async function fetchActivityFeed({ limit, before } = {}) {
  const params = new URLSearchParams();
  if (typeof limit === "number") params.set("limit", String(limit));
  if (before) params.set("before", before);
  const qs = params.toString();
  return requestJson(`/activity-feed${qs ? `?${qs}` : ""}`);
}

// ── Chat ────────────────────────────────────────────────────
export async function startChatTurn({ turnId = "", session = "mobile", scope = "chat", subjectId = "", subjectTitle = "", message }) {
  return requestJson("/chat/turns", json({
    turn_id: turnId,
    session,
    scope,
    subject_id: subjectId,
    subject_title: subjectTitle,
    message,
  }));
}

export async function fetchChatTurn(turnId) {
  return requestJson(`/chat/turns/${encodeURIComponent(turnId)}`);
}

export async function fetchChatTurns({ session = "mobile", scope = "", limit = 50 } = {}) {
  const params = new URLSearchParams();
  params.set("session", session);
  if (scope) params.set("scope", scope);
  if (typeof limit === "number") params.set("limit", String(Math.max(1, Math.floor(limit))));
  return requestJson(`/chat/turns?${params.toString()}`);
}

// ── Feedback ───────────────────────────────────────────────
export async function submitFeedback(payload) {
  return requestJson("/feedback", json(payload));
}

// ── Delight Ack ────────────────────────────────────────────
export async function markDelightSent(bvid) {
  return requestJson("/delight/sent", json({ bvid }));
}

// ── Refresh ────────────────────────────────────────────────
export async function refreshRecommendations() {
  return requestJson("/recommendations/refresh", { method: "POST" });
}

// ── Interest Probes ─────────────────────────────────────────
export async function respondToProbe(domain, responseType, message = "") {
  return requestJson("/interest-probes/respond", {
    ...json({ domain, response: responseType, message }),
    timeoutMs: 35_000,
  });
}
