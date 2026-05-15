import { normalizeRecommendation } from "./popup-helpers.js";

const BACKEND_URL = "http://127.0.0.1:8420/api";

async function requestJson(path, options = {}) {
  const response = await fetch(`${BACKEND_URL}${path}`, options);
  if (!response.ok) {
    throw new Error(`${path} request failed: ${response.status}`);
  }
  return response.json();
}

export async function checkBackendStatus() {
  try {
    const response = await fetch(`${BACKEND_URL}/health`, { method: "GET" });
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
  return requestJson("/config?reveal_keys=true", { method: "GET" });
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
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(data),
  });
}
