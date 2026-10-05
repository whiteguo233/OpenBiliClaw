/** Bounded, read-only Instagram browser task executor. */

import {
  clearInstagramResponseBuffer,
  readInstagramResponseBuffer,
  requestInstagramResponseReplay,
} from "./response-buffer.ts";
import { isInstagramTaskTabLocation } from "./task-mode.ts";
import { parseInstagramViewer } from "./viewer.ts";
import {
  instagramCanonicalUrl,
  instagramMediaIdentity,
  isInstagramHost,
  type InstagramContentType,
} from "../../shared/platforms/instagram.ts";

export type InstagramBootstrapScope =
  | "instagram_liked"
  | "instagram_saved"
  | "instagram_following";
export type InstagramDiscoverMode = "topic" | "creator";

interface InstagramTaskBase {
  id: string;
  claim_token: string;
}

export interface InstagramDiscoverTask extends InstagramTaskBase {
  type: "discover";
  mode: InstagramDiscoverMode;
  query?: string;
  username?: string;
  cursor?: string;
  source_keyword_id?: number;
  max_items: number;
  max_pages: number;
  request_interval_ms?: number;
}

export interface InstagramBootstrapTask extends InstagramTaskBase {
  type: "bootstrap_events";
  scopes: InstagramBootstrapScope[];
  max_items_per_scope: number;
  max_pages_per_scope: number;
  request_interval_ms?: number;
}

export type InstagramTask = InstagramDiscoverTask | InstagramBootstrapTask;

export interface InstagramWireItem {
  scope?: InstagramBootstrapScope;
  id: string;
  code?: string;
  content_type: InstagramContentType;
  url: string;
  title?: string;
  description?: string;
  cover_url?: string;
  author_id?: string;
  author_name?: string;
  published_at?: string;
}

export interface InstagramTaskResult {
  task_id: string;
  claim_token: string;
  status: "ok" | "empty" | "partial" | "failed";
  items: InstagramWireItem[];
  scope_counts: Record<string, number>;
  scope_complete: Record<string, boolean>;
  account_id?: string;
  error?: string;
  debug?: Record<string, unknown>;
}

export interface InstagramTaskProgress {
  task_id: string;
  claim_token: string;
  phase: "identity" | "discover" | "bootstrap";
  scope?: InstagramBootstrapScope;
  page?: number;
  item_count?: number;
  cursor?: string;
  accepted: boolean;
  items?: InstagramWireItem[];
  scope_counts?: Record<string, number>;
  scope_complete?: Record<string, boolean>;
  account_id?: string;
  debug?: Record<string, unknown>;
}

type FetchLike = ((input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) & {
  waitForTurn?: () => Promise<void>;
};

interface ApiEnvelope {
  kind:
    | "ok"
    | "login_required"
    | "challenge"
    | "rate_limited"
    | "html_response"
    | "invalid_json"
    | "http_error"
    | "network_error";
  payload?: Record<string, unknown>;
  error?: string;
}

interface LaneOutcome {
  items: InstagramWireItem[];
  complete: boolean;
  accepted: boolean;
  error?: string;
  identity_failure?: IdentityVerificationFailure;
}

interface IdentityVerificationFailure {
  error: string;
  accountId?: string;
  changed: boolean;
}

const PRIVATE_HEADERS = {
  accept: "application/json, text/javascript, */*; q=0.01",
  "x-ig-app-id": "936619743392459",
  "x-requested-with": "XMLHttpRequest",
};
const API_ROOT = "https://www.instagram.com";
const MAX_WIRE_ITEMS = 10_000;
const LIKED_RECENT_LIMIT = 300;
// 2026-10-03 live cap=20 repeated the same two Likes for all 20 scroll
// observations (43 identity reads). Three consecutive unchanged observations
// after accepted data bound this best-effort lane; this is NOT terminal proof.
const LIKED_STALLED_OBSERVATIONS = 3;
const API_RESPONSE_LIMIT_BYTES = 2 * 1024 * 1024;
export const INSTAGRAM_API_REQUEST_TIMEOUT_MS = 20_000;
const KNOWN_SCOPES: readonly InstagramBootstrapScope[] = [
  "instagram_liked",
  "instagram_saved",
  "instagram_following",
];

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function text(value: unknown, limit = 6000): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return typeof value === "string" ? value.trim().slice(0, limit) : "";
}

function integer(value: unknown, fallback: number, maximum = MAX_WIRE_ITEMS): number {
  const parsed = Math.floor(Number(value));
  return Number.isFinite(parsed) && parsed > 0 ? Math.min(maximum, parsed) : fallback;
}

function pacedFetch(
  fetcher: FetchLike,
  intervalMs: number,
  sleeper: (milliseconds: number) => Promise<void>,
): FetchLike {
  let lastStartedAt: number | null = null;
  const paced: FetchLike = (input, init) => fetcher(input, init);
  paced.waitForTurn = async () => {
    if (lastStartedAt !== null) {
      const remaining = intervalMs - (Date.now() - lastStartedAt);
      if (remaining > 0) await sleeper(remaining);
    }
    lastStartedAt = Date.now();
  };
  return paced;
}

function imageUrl(media: Record<string, unknown>): string {
  const versions = record(media.image_versions2);
  const candidates = Array.isArray(versions?.candidates) ? versions.candidates : [];
  const candidate = record(candidates[0]);
  return text(media.display_uri || media.thumbnail_url || candidate?.url, 4096);
}

function timestamp(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    const date = new Date(value < 10_000_000_000 ? value * 1000 : value);
    return Number.isNaN(date.getTime()) ? "" : date.toISOString();
  }
  if (typeof value === "string" && value.trim()) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : date.toISOString();
  }
  return "";
}

export function normalizeInstagramMedia(
  value: unknown,
  scope?: InstagramBootstrapScope,
): InstagramWireItem | null {
  const outer = record(value);
  const media = record(outer?.media) || record(outer?.node) || outer;
  if (!media) return null;
  const id = instagramMediaIdentity(media.pk || media.id || media.media_id);
  const code = text(media.code || media.shortcode, 128);
  if (!id || !code) return null;
  const user = record(media.user) || record(media.owner);
  const caption = record(media.caption);
  const description = text(caption?.text || media.accessibility_caption, 6000);
  const mediaType = Number(media.media_type);
  const typename = text(media.__typename, 128).toLowerCase();
  const productType = text(media.product_type, 128).toLowerCase();
  const contentType: InstagramContentType =
    Array.isArray(media.carousel_media) || mediaType === 8 || typename.includes("sidecar")
      ? "carousel"
      : mediaType === 2 || productType.includes("clip") || typename.includes("video")
        ? "reel"
        : "post";
  const authorId = instagramMediaIdentity(user?.pk || user?.id);
  const authorName = text(user?.username, 128);
  const publishedAt = timestamp(media.taken_at || media.taken_at_timestamp || media.date);
  const cover = imageUrl(media);
  return {
    ...(scope ? { scope } : {}),
    id,
    code,
    content_type: contentType,
    url: instagramCanonicalUrl(contentType, code),
    ...(description ? { title: description.slice(0, 300), description } : {}),
    ...(cover ? { cover_url: cover } : {}),
    ...(authorId ? { author_id: authorId } : {}),
    ...(authorName ? { author_name: authorName } : {}),
    ...(publishedAt ? { published_at: publishedAt } : {}),
  };
}

export function normalizeInstagramFollowingUser(value: unknown): InstagramWireItem | null {
  const user = record(value);
  if (!user) return null;
  const id = instagramMediaIdentity(user.pk || user.id);
  const username = text(user.username, 128);
  if (!id || !username) return null;
  const fullName = text(user.full_name, 300);
  const cover = text(user.profile_pic_url, 4096);
  return {
    scope: "instagram_following",
    id,
    content_type: "user",
    url: instagramCanonicalUrl("user", username),
    author_id: id,
    author_name: username,
    ...(fullName ? { title: fullName } : {}),
    ...(cover ? { cover_url: cover } : {}),
  };
}

export function instagramPaginatedUrl(path: string, cursor?: string): string {
  const url = new URL(path, API_ROOT);
  // The first request deliberately omits max_id. Instagram treats an empty
  // max_id differently from an absent one on some private-web deployments.
  if (cursor) url.searchParams.set("max_id", cursor);
  return url.href;
}

function payloadFailureKind(payload: Record<string, unknown>): ApiEnvelope["kind"] | null {
  const status = text(payload.status, 64).toLowerCase();
  if (status !== "fail" && status !== "error") return null;
  // Field names (notably a null checkpoint_url) are not failure evidence.
  const diagnostic = [payload.message, payload.error_type, payload.checkpoint_url]
    .filter((value): value is string => typeof value === "string")
    .join(" ").toLowerCase();
  if (/rate.limit|too.many|wait.a.few.minutes|please.wait/.test(diagnostic)) {
    return "rate_limited";
  }
  if (/challenge|checkpoint|captcha|feedback(?:_| )?required/.test(diagnostic)) {
    return "challenge";
  }
  if (/login|logged.out|not.authorized/.test(diagnostic)) return "login_required";
  return "http_error";
}

export function classifyInstagramApiEnvelope(
  status: number,
  contentType: string,
  payload: unknown,
  responseUrl = "",
): ApiEnvelope {
  if (status === 429) return { kind: "rate_limited", error: "rate_limited" };
  // A SPA challenge/login shell may have no visible text. The final response
  // route is stronger evidence than strings inside its application bundle.
  try {
    const url = new URL(responseUrl);
    if (url.protocol === "https:" && isInstagramHost(url.hostname)) {
      if (/^\/(?:challenge|checkpoint)(?:\/|$)/i.test(url.pathname)) {
        return { kind: "challenge", error: "challenge_required" };
      }
      if (/^\/accounts\/login(?:\/|$)/i.test(url.pathname)) {
        return { kind: "login_required", error: "login_required" };
      }
    }
  } catch {
    // Synthetic/legacy Response values can omit URL; classify their payload.
  }
  if (/text\/html/i.test(contentType)) {
    // Homepage/login bundles contain challenge, captcha and login route names
    // even when no such page is displayed. Only inspect visible markup here.
    const html = typeof payload === "string" ? payload.toLowerCase()
      .replace(/<!--[^]*?-->/g, " ")
      .replace(/<(script|style|template|noscript)\b[^>]*>[^]*?<\/\1\s*>/g, " ")
      .slice(0, 100_000) : "";
    const visible = html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    const challengeForm = /<form\b[^>]*\baction\s*=\s*["'][^"']*\/(?:challenge|checkpoint)(?:[/?#"'])/.test(html);
    const loginForm = /<form\b[^>]*\baction\s*=\s*["'][^"']*\/accounts\/login(?:[/?#"'])/.test(html)
      || /<input\b[^>]*\btype\s*=\s*["']password["']/.test(html);
    if (/rate.limit|too.many.requests|wait.a.few.minutes/.test(visible)) {
      return { kind: "rate_limited", error: "rate_limited" };
    }
    if (challengeForm || /challenge[_\s-]+required|checkpoint[_\s-]+required|captcha[_\s-]+required|feedback[_\s-]+required/.test(visible)) {
      return { kind: "challenge", error: "challenge_required" };
    }
    if (status === 401 || status === 403 || loginForm || /session expired/.test(visible)) {
      return { kind: "login_required", error: "login_required" };
    }
    return { kind: "html_response", error: "html_response" };
  }
  const body = record(payload);
  if (!body) return { kind: "invalid_json", error: "invalid_json" };
  const failure = payloadFailureKind(body);
  if (failure) {
    return { kind: failure, error: failure === "challenge" ? "challenge_required" : failure };
  }
  if (status === 401 || status === 403) {
    return { kind: "login_required", error: "login_required" };
  }
  if (status < 200 || status >= 300) return { kind: "http_error", error: `http_${status}` };
  return { kind: "ok", payload: body };
}

async function boundedResponseText(response: Response, maximumBytes: number): Promise<string | null> {
  if (!response.body) {
    const value = await response.text();
    return new TextEncoder().encode(value).byteLength <= maximumBytes ? value : null;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let total = 0;
  let output = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maximumBytes) {
        await reader.cancel().catch(() => {});
        return null;
      }
      output += decoder.decode(value, { stream: true });
    }
    output += decoder.decode();
    return output;
  } finally {
    reader.releaseLock();
  }
}

async function fetchApi(url: string, fetcher: FetchLike): Promise<ApiEnvelope> {
  await fetcher.waitForTurn?.();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), INSTAGRAM_API_REQUEST_TIMEOUT_MS);
  try {
    const response = await fetcher(url, {
      method: "GET",
      credentials: "include",
      headers: PRIVATE_HEADERS,
      cache: "no-store",
      signal: controller.signal,
    });
    const contentType = response.headers.get("content-type") || "";
    const declaredLength = Number(response.headers.get("content-length") || 0);
    if (Number.isFinite(declaredLength) && declaredLength > API_RESPONSE_LIMIT_BYTES) {
      return { kind: "invalid_json", error: "response_too_large" };
    }
    const body = await boundedResponseText(response, API_RESPONSE_LIMIT_BYTES);
    if (body === null) return { kind: "invalid_json", error: "response_too_large" };
    if (/text\/html/i.test(contentType)) {
      return classifyInstagramApiEnvelope(response.status, contentType, body, response.url);
    }
    if (!/(?:application\/json|text\/javascript)/i.test(contentType)) {
      if ([401, 403, 429].includes(response.status)) {
        return classifyInstagramApiEnvelope(response.status, contentType, {}, response.url);
      }
      return { kind: "invalid_json", error: "unexpected_mime" };
    }
    let payload: unknown;
    try {
      payload = JSON.parse(body) as unknown;
    } catch {
      return { kind: "invalid_json", error: "invalid_json" };
    }
    return classifyInstagramApiEnvelope(response.status, contentType, payload, response.url);
  } catch {
    return controller.signal.aborted
      ? { kind: "network_error", error: "request_timeout" }
      : { kind: "network_error", error: "network_error" };
  } finally {
    clearTimeout(timer);
  }
}

async function postProgress(progress: InstagramTaskProgress): Promise<boolean> {
  if (typeof chrome === "undefined" || !chrome.runtime?.sendMessage) return true;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await chrome.runtime.sendMessage({
        action: "INSTAGRAM_TASK_PROGRESS",
        data: progress,
      }) as { ok?: boolean } | undefined;
      if (response?.ok === true) return true;
    } catch {
      // A cold worker may need one recovery turn before accepting progress.
    }
    if (attempt < 2) await sleep(250 * 2 ** attempt);
  }
  return false;
}

function lanePath(scope: InstagramBootstrapScope, accountId: string): string {
  if (scope === "instagram_saved") return "/api/v1/feed/saved/posts/";
  return `/api/v1/friendships/${encodeURIComponent(accountId)}/following/`;
}

async function executeLane(
  task: InstagramBootstrapTask,
  scope: InstagramBootstrapScope,
  accountId: string,
  fetcher: FetchLike,
  sleeper: (milliseconds: number) => Promise<void>,
  verifyIdentity: () => Promise<IdentityVerificationFailure | null>,
): Promise<LaneOutcome> {
  if (scope === "instagram_liked") {
    return executeLikedLane(task, accountId, sleeper, verifyIdentity);
  }
  const limit = integer(task.max_items_per_scope, 300);
  const maxPages = integer(task.max_pages_per_scope, 20, 100);
  const items = new Map<string, InstagramWireItem>();
  const cursors = new Set<string>();
  let cursor = "";
  let accepted = false;
  for (let page = 1; page <= maxPages; page += 1) {
    const beforeRequest = await verifyIdentity();
    if (beforeRequest) {
      return {
        items: [...items.values()],
        complete: false,
        accepted,
        error: beforeRequest.error,
        identity_failure: beforeRequest,
      };
    }
    const envelope = await fetchApi(instagramPaginatedUrl(lanePath(scope, accountId), cursor), fetcher);
    if (envelope.kind !== "ok" || !envelope.payload) {
      const error = envelope.kind === "challenge" ? "challenge_required" : envelope.error || envelope.kind;
      return { items: [...items.values()], complete: false, accepted, error };
    }
    const afterRequest = await verifyIdentity();
    if (afterRequest) {
      return {
        items: [...items.values()],
        complete: false,
        accepted,
        error: afterRequest.error,
        identity_failure: afterRequest,
      };
    }
    const rawRows = scope === "instagram_following"
      ? envelope.payload.users
      : envelope.payload.items;
    if (!Array.isArray(rawRows)) {
      return { items: [...items.values()], complete: false, accepted, error: "items_envelope_missing" };
    }
    let rejected = 0;
    for (const row of rawRows) {
      const item = scope === "instagram_following"
        ? normalizeInstagramFollowingUser(row)
        : normalizeInstagramMedia(row, scope);
      if (!item) { rejected += 1; continue; }
      if (items.has(item.id)) continue;
      if (items.size >= limit) break;
      items.set(item.id, item);
    }
    accepted ||= rawRows.length === 0 || items.size > 0;
    const next = text(envelope.payload.next_max_id || envelope.payload.max_id, 1024);
    const more = envelope.payload.more_available;
    const terminal = more === false || (!next && more !== true);
    const capped = items.size >= limit;
    const progressPersisted = await postProgress({
      task_id: task.id,
      claim_token: task.claim_token,
      phase: "bootstrap",
      scope,
      page,
      item_count: items.size,
      items: [...items.values()],
      scope_counts: { [scope]: items.size },
      scope_complete: { [scope]: terminal && !capped && rejected === 0 },
      account_id: accountId,
      ...(next ? { cursor: next } : {}),
      accepted,
    });
    if (!progressPersisted) {
      return {
        items: [...items.values()],
        complete: false,
        accepted: true,
        error: "progress_persistence_unavailable",
      };
    }
    if (rejected) {
      return { items: [...items.values()], complete: false, accepted,
        error: items.size ? "response_schema_degraded" : "response_rows_rejected" };
    }
    if (capped) {
      return {
        items: [...items.values()],
        complete: false,
        accepted: true,
        error: "item_cap_reached",
      };
    }
    if (terminal) return { items: [...items.values()], complete: true, accepted: true };
    if (!next) {
      return { items: [...items.values()], complete: false, accepted: true, error: "next_cursor_missing" };
    }
    if (cursors.has(next) || next === cursor) {
      return { items: [...items.values()], complete: false, accepted: true, error: "duplicate_cursor" };
    }
    cursors.add(next);
    cursor = next;
  }
  return { items: [...items.values()], complete: false, accepted, error: "page_cap_reached" };
}

async function executeLikedLane(
  task: InstagramBootstrapTask,
  accountId: string,
  sleeper: (milliseconds: number) => Promise<void>,
  verifyIdentity: () => Promise<IdentityVerificationFailure | null>,
): Promise<LaneOutcome> {
  if (typeof window === "undefined" || !isInstagramTaskTabLocation()
    || window.location.pathname.replace(/\/$/, "") !== "/your_activity/interactions/likes") {
    return { items: [], complete: false, accepted: false, error: "response_envelope_unobserved" };
  }
  const limit = Math.min(LIKED_RECENT_LIMIT, integer(task.max_items_per_scope, 300));
  const items = new Map<string, InstagramWireItem>();
  let accepted = false;
  let stalledObservations = 0;
  for (let page = 1; page <= integer(task.max_pages_per_scope, 20, 100); page++) {
    const previousCount = items.size;
    const knownFailure = readInstagramResponseBuffer()
      .filter(entry => entry.route === "liked")
      .find(entry => entry.error)?.error;
    if (knownFailure) {
      return { items: [...items.values()], complete: false, accepted, error: knownFailure };
    }
    const beforeObservation = await verifyIdentity();
    if (beforeObservation) {
      return {
        items: [...items.values()],
        complete: false,
        accepted,
        error: beforeObservation.error,
        identity_failure: beforeObservation,
      };
    }
    requestInstagramResponseReplay();
    await sleeper(page === 1 ? 1500 : Math.max(1000, integer(task.request_interval_ms, 3000, 30000)));
    requestInstagramResponseReplay();
    const envelopes = readInstagramResponseBuffer().filter(entry => entry.route === "liked");
    const upstreamFailure = envelopes.find(entry => entry.error)?.error;
    if (upstreamFailure) {
      return { items: [...items.values()], complete: false, accepted, error: upstreamFailure };
    }
    const afterObservation = await verifyIdentity();
    if (afterObservation) {
      return {
        items: [...items.values()],
        complete: false,
        accepted,
        error: afterObservation.error,
        identity_failure: afterObservation,
      };
    }
    const valid = envelopes.filter(entry => entry.shape_valid === true);
    accepted ||= valid.length > 0;
    for (const envelope of valid) for (const item of envelope.items) {
      if (items.size < limit) items.set(item.id, { ...item, scope: "instagram_liked" });
    }
    const rejected = envelopes.some(entry => entry.shape_valid === false || (entry.rejected_count || 0) > 0);
    const complete = valid.some(entry => entry.affirmative_terminal === true) && !items.size && !rejected;
    if (accepted) {
      const persisted = await postProgress({ task_id: task.id, claim_token: task.claim_token,
        phase: "bootstrap", scope: "instagram_liked", page, item_count: items.size,
        items: [...items.values()], scope_counts: { instagram_liked: items.size },
        scope_complete: { instagram_liked: complete }, account_id: accountId, accepted: true });
      if (!persisted) return { items: [...items.values()], complete: false, accepted, error: "progress_persistence_unavailable" };
    }
    if (complete) return { items: [], complete: true, accepted: true };
    if (rejected) return { items: [...items.values()], complete: false, accepted, error: "response_schema_degraded" };
    if (items.size >= limit) return { items: [...items.values()], complete: false, accepted, error: "item_cap_reached" };
    const failure = classifyInstagramDiscoverPage(window.location.pathname, document.title, document.body?.innerText || "");
    if (failure) return { items: [...items.values()], complete: false, accepted, error: failure };
    stalledObservations = items.size > 0 && items.size === previousCount
      ? stalledObservations + 1 : 0;
    if (stalledObservations >= LIKED_STALLED_OBSERVATIONS) {
      return { items: [...items.values()], complete: false, accepted, error: "progress_stalled" };
    }
    window.scrollTo(0, document.documentElement.scrollHeight);
  }
  return { items: [...items.values()], complete: false, accepted,
    error: accepted ? "page_cap_reached" : "response_envelope_unobserved" };
}

async function resolveFreshViewer(
  fetcher: FetchLike,
  username: string,
): Promise<{ accountId: string; error?: string }> {
  await fetcher.waitForTurn?.();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), INSTAGRAM_API_REQUEST_TIMEOUT_MS);
  try {
    // Fresh same-session SSR avoids trusting a stale tab after an account switch.
    const response = await fetcher(`${API_ROOT}/`, {
      method: "GET", credentials: "include", cache: "no-store",
      headers: { accept: "text/html" }, signal: controller.signal,
    });
    const body = await boundedResponseText(response, 4 * 1024 * 1024);
    if (body === null) return { accountId: "", error: "response_too_large" };
    const classified = classifyInstagramApiEnvelope(response.status,
      response.headers.get("content-type") || "", body, response.url);
    if (classified.kind !== "html_response" || !response.ok) {
      return { accountId: "", error: classified.error || classified.kind };
    }
    const accountId = parseInstagramViewer(body, username);
    return accountId ? { accountId }
      : { accountId: "", error: "account_identity_missing" };
  } catch {
    return { accountId: "", error: "network_error" };
  } finally { clearTimeout(timer); }
}

async function resolveIdentity(fetcher: FetchLike): Promise<{
  accountId: string;
  username: string;
  error?: string;
}> {
  const envelope = await fetchApi(`${API_ROOT}/api/v1/accounts/edit/web_form_data/`, fetcher);
  if (envelope.kind !== "ok" || !envelope.payload) {
    const error = envelope.kind === "challenge" ? "challenge_required" : envelope.error || envelope.kind;
    return { accountId: "", username: "", error };
  }
  const username = text(record(envelope.payload.form_data)?.username, 128);
  if (envelope.payload.status !== "ok" || !username) {
    return { accountId: "", username: "", error: "account_identity_missing" };
  }
  const viewer = await resolveFreshViewer(fetcher, username);
  return viewer.accountId
    ? { accountId: viewer.accountId, username }
    : { accountId: "", username: "", error: viewer.error || "account_identity_missing" };
}

async function verifyFrozenIdentity(
  fetcher: FetchLike,
  expectedAccountId: string,
  expectedUsername: string,
): Promise<IdentityVerificationFailure | null> {
  // The initial exact identity already paired the account endpoint username with
  // a fresh PolarisViewer.  A normal recheck needs only another no-store viewer
  // document.  If it no longer matches, resolve the full pair once to distinguish
  // a real account switch from a transient/unavailable identity response.
  const viewer = await resolveFreshViewer(fetcher, expectedUsername);
  if (viewer.accountId === expectedAccountId) return null;
  if (viewer.accountId) {
    return {
      error: "instagram_account_changed",
      accountId: viewer.accountId,
      changed: true,
    };
  }
  if (viewer.error !== "account_identity_missing") {
    return {
      error: viewer.error || "account_identity_missing",
      changed: false,
    };
  }
  const current = await resolveIdentity(fetcher);
  if (!current.accountId) {
    return {
      error: current.error || viewer.error || "account_identity_missing",
      changed: false,
    };
  }
  if (current.accountId !== expectedAccountId) {
    return {
      error: "instagram_account_changed",
      accountId: current.accountId,
      changed: true,
    };
  }
  return null;
}

function loadedDocumentViewerId(username: string): string | null {
  if (typeof document === "undefined" || !document.querySelectorAll) return null;
  const maximumBytes = 4 * 1024 * 1024;
  let totalBytes = 0;
  let html = "";
  for (const script of Array.from(document.querySelectorAll("script[data-sjs]"))) {
    const body = script.textContent || "";
    totalBytes += new TextEncoder().encode(body).byteLength;
    if (totalBytes > maximumBytes) return "";
    html += `<script data-sjs>${body}</script>`;
  }
  return parseInstagramViewer(html, username);
}

function identityFailureResult(
  task: InstagramBootstrapTask,
  error: string,
  accountId?: string,
): InstagramTaskResult {
  return {
    task_id: task.id,
    claim_token: task.claim_token,
    status: "failed",
    items: [],
    scope_counts: Object.fromEntries(task.scopes.map((scope) => [scope, 0])),
    scope_complete: Object.fromEntries(task.scopes.map((scope) => [scope, false])),
    ...(accountId ? { account_id: accountId } : {}),
    error,
    debug: {
      identity_resolved: Boolean(accountId),
      response_observed: false,
      failures: [error],
    },
  };
}

function accountChangedResult(
  task: InstagramBootstrapTask,
  accountId: string,
): InstagramTaskResult {
  return identityFailureResult(task, "instagram_account_changed", accountId);
}

export async function executeInstagramBootstrap(
  task: InstagramBootstrapTask,
  fetcher: FetchLike = globalThis.fetch.bind(globalThis),
  sleeper: (milliseconds: number) => Promise<void> = sleep,
): Promise<InstagramTaskResult> {
  await postProgress({
    task_id: task.id,
    claim_token: task.claim_token,
    phase: "identity",
    accepted: false,
  });
  const requestIntervalMs = integer(task.request_interval_ms, 3_000, 30_000);
  const taskFetcher = pacedFetch(fetcher, requestIntervalMs, sleeper);
  const identity = await resolveIdentity(taskFetcher);
  if (!identity.accountId) {
    return {
      task_id: task.id,
      claim_token: task.claim_token,
      status: "failed",
      items: [],
      scope_counts: {},
      scope_complete: Object.fromEntries(task.scopes.map((scope) => [scope, false])),
      error: identity.error || "account_identity_missing",
      debug: { identity_resolved: false, response_observed: false },
    };
  }
  if (task.scopes.includes("instagram_liked")) {
    const loadedAccountId = loadedDocumentViewerId(identity.username);
    if (loadedAccountId !== null && loadedAccountId !== identity.accountId) {
      return identityFailureResult(task, "account_identity_missing", identity.accountId);
    }
  }
  const identityPersisted = await postProgress({
    task_id: task.id,
    claim_token: task.claim_token,
    phase: "identity",
    item_count: 0,
    account_id: identity.accountId,
    accepted: true,
  });
  if (!identityPersisted) {
    return {
      task_id: task.id,
      claim_token: task.claim_token,
      status: "failed",
      items: [],
      scope_counts: Object.fromEntries(task.scopes.map((scope) => [scope, 0])),
      scope_complete: Object.fromEntries(task.scopes.map((scope) => [scope, false])),
      account_id: identity.accountId,
      error: "progress_persistence_unavailable",
      debug: { identity_resolved: true, response_observed: false },
    };
  }
  const allItems: InstagramWireItem[] = [];
  const scopeCounts: Record<string, number> = {};
  const scopeComplete: Record<string, boolean> = {};
  const failures: string[] = [];
  let acceptedScopes = 0;
  let haltedAt = -1;
  const verifyIdentity = () => verifyFrozenIdentity(
    taskFetcher,
    identity.accountId,
    identity.username,
  );
  for (const [scopeIndex, scope] of task.scopes.entries()) {
    const lane = await executeLane(
      task,
      scope,
      identity.accountId,
      taskFetcher,
      sleeper,
      verifyIdentity,
    );
    if (lane.identity_failure?.changed && lane.identity_failure.accountId) {
      return accountChangedResult(task, lane.identity_failure.accountId);
    }
    allItems.push(...lane.items);
    scopeCounts[scope] = lane.items.length;
    scopeComplete[scope] = lane.complete;
    if (lane.accepted) acceptedScopes += 1;
    const fatal = Boolean(lane.identity_failure)
      || Boolean(
        lane.error
        && /^(?:challenge_required|rate_limited|login_required)$/.test(lane.error),
      );
    if (lane.error) failures.push(fatal ? lane.error : `${scope}:${lane.error}`);
    if (fatal) {
      haltedAt = scopeIndex;
      break;
    }
    if (scope !== task.scopes[task.scopes.length - 1]) {
      await sleeper(Math.max(1_000, integer(task.request_interval_ms, 3_000, 30_000)));
    }
  }
  if (haltedAt < 0) {
    const beforeFinal = await verifyIdentity();
    if (beforeFinal?.changed && beforeFinal.accountId) {
      return accountChangedResult(task, beforeFinal.accountId);
    }
    if (beforeFinal) {
      failures.push(beforeFinal.error);
      for (const scope of task.scopes) scopeComplete[scope] = false;
    }
  }
  if (haltedAt >= 0) {
    for (const remainingScope of task.scopes.slice(haltedAt + 1)) {
      scopeCounts[remainingScope] = 0;
      scopeComplete[remainingScope] = false;
    }
  }
  const status: InstagramTaskResult["status"] = failures.length
    ? (allItems.length || acceptedScopes ? "partial" : "failed")
    : allItems.length
      ? "ok"
      : "empty";
  return {
    task_id: task.id,
    claim_token: task.claim_token,
    status,
    items: allItems.slice(0, MAX_WIRE_ITEMS),
    scope_counts: scopeCounts,
    scope_complete: scopeComplete,
    account_id: identity.accountId,
    ...(failures.length ? { error: failures[0] } : {}),
    debug: {
      identity_resolved: true,
      failures,
      response_observed: acceptedScopes > 0,
      terminal_evidence: Object.entries(scopeComplete)
        .filter(([, complete]) => complete)
        .map(([scope]) => scope)
        .join(",") || "accepted_nonterminal_scope_page",
    },
  };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function discoverRouteMatches(task: InstagramDiscoverTask): boolean {
  try {
    const url = new URL(window.location.href);
    if (!isInstagramHost(url.hostname) || !isInstagramTaskTabLocation(url)) return false;
    const path = decodeURIComponent(url.pathname).replace(/\/+$/, "").toLowerCase();
    if (task.mode === "topic") return path.startsWith("/popular/") || path.startsWith("/explore/tags/");
    const username = text(task.username, 128).toLowerCase();
    return Boolean(username) && path === `/${username}`;
  } catch {
    return false;
  }
}

export function explicitInstagramDiscoverEmpty(bodyValue: string): boolean {
  return /(?:^|\n)\s*(?:no posts yet|没有帖子|尚无帖子)\s*(?:\n|$)/i.test(bodyValue);
}

function explicitDiscoverEmpty(): boolean {
  const body = text(document.body?.innerText || document.body?.textContent, 20_000);
  return explicitInstagramDiscoverEmpty(body);
}

type DiscoverPageFailure = "" | "public_page_unavailable" | "login_required"
  | "challenge_required" | "rate_limited";

/** Classify explicit page-level evidence, never caption text or missing data. */
export function classifyInstagramDiscoverPage(
  pathname: string,
  title: string,
  body: string,
): DiscoverPageFailure {
  if (/^\/(?:challenge|checkpoint)(?:\/|$)/i.test(pathname)) return "challenge_required";
  if (/^\/accounts\/login(?:\/|$)/i.test(pathname)) return "login_required";
  if (/(?:^|\n)\s*(?:please wait a few minutes before you try again\.?|请稍等几分钟再试。?)\s*(?:\n|$)/i.test(body)) {
    return "rate_limited";
  }
  // A real 200 soft-404 has the dedicated title AND the broken-link message.
  // A normal logged-out page has "登录" too; that alone is not a login wall.
  if (/^(?:Page无法访问|页面无法访问|Page (?:not found|isn't available)|Sorry, this page isn't available)\s*[.!。]?(?:\s*[•|–-]\s*Instagram)?$/i.test(title.trim())
    && /链接可能已损坏或主页被移除|the link you followed may be broken|the page may have been removed/i.test(body)) {
    return "public_page_unavailable";
  }
  return "";
}

function currentDiscoverPageFailure(): DiscoverPageFailure {
  return classifyInstagramDiscoverPage(
    window.location.pathname,
    document.title,
    text(document.body?.innerText || document.body?.textContent, 20_000),
  );
}

/** Build a terminal discovery callback only from observed normalized envelopes. */
export function buildInstagramDiscoverResult(
  task: InstagramDiscoverTask,
  envelopes: readonly import("./response-buffer.ts").InstagramObservedEnvelope[],
  explicitEmpty = false,
  pageFailure: DiscoverPageFailure = "",
): InstagramTaskResult {
  const maxItems = integer(task.max_items, 30, 300);
  const items = new Map<string, InstagramWireItem>();
  const collections = new Map<string, {
    observed: number;
    rejected: number;
    accepted: number;
    shapeValid: boolean;
    terminal: boolean;
  }>();
  let relevantEnvelopeCount = 0;
  let upstreamError = "";
  let cursor = "";
  for (const envelope of envelopes) {
    if (envelope.route !== task.mode && envelope.route !== "unknown") continue;
    if (envelope.error && !upstreamError) upstreamError = envelope.error;
    relevantEnvelopeCount += 1;
    const collectionId = text(
      envelope.collection_id || `${envelope.route}:legacy`,
      256,
    );
    const summary = collections.get(collectionId) || {
      observed: 0,
      rejected: 0,
      accepted: 0,
      shapeValid: true,
      terminal: false,
    };
    const observed = Number.isInteger(envelope.observed_count)
      ? Math.max(0, Number(envelope.observed_count))
      : envelope.items.length;
    const rejected = Number.isInteger(envelope.rejected_count)
      ? Math.max(0, Number(envelope.rejected_count))
      : 0;
    summary.observed += observed;
    summary.rejected += rejected;
    summary.accepted += envelope.items.length;
    summary.shapeValid = summary.shapeValid
      && envelope.shape_valid !== false
      && rejected <= observed;
    if (envelope.affirmative_terminal === true || envelope.has_next_page === false) {
      summary.terminal = true;
    }
    collections.set(collectionId, summary);
    for (const item of envelope.items) {
      if (!item.id || items.has(item.id) || items.size >= maxItems) continue;
      items.set(item.id, item);
    }
    if (envelope.cursor) cursor = envelope.cursor;
  }
  const rows = [...items.values()];
  const responseObserved = relevantEnvelopeCount > 0;
  const summaries = [...collections.values()];
  const observedCount = summaries.reduce((total, value) => total + value.observed, 0);
  const rejectedCount = summaries.reduce((total, value) => total + value.rejected, 0);
  const schemaDegraded = summaries.some((value) =>
    !value.shapeValid || (value.observed > 0 && value.accepted === 0 && value.rejected >= value.observed));
  const terminalCollections = summaries.filter((value) => value.terminal).length;
  const allCollectionsTerminal = summaries.length > 0
    && terminalCollections === summaries.length;
  const capped = rows.length >= maxItems;
  const complete = responseObserved
    && !upstreamError
    && !pageFailure
    && !schemaDegraded
    && !capped
    && (allCollectionsTerminal || explicitEmpty);
  const affirmativeEmpty = rows.length === 0 && complete;
  const status: InstagramTaskResult["status"] = rows.length
    ? (complete ? "ok" : "partial")
    : affirmativeEmpty
      ? "empty"
      : "failed";
  const error = upstreamError || pageFailure || (schemaDegraded
    ? (rows.length ? "response_schema_degraded" : "response_rows_rejected")
    : capped
      ? "item_cap_reached"
      : task.cursor
        ? "cursor_resume_not_observed"
        : rows.length
          ? "bounded_public_snapshot"
          : "response_envelope_unobserved");
  return {
    task_id: task.id,
    claim_token: task.claim_token,
    status,
    items: rows,
    scope_counts: { discover: rows.length },
    scope_complete: { discover: complete || affirmativeEmpty },
    ...(status === "partial" || status === "failed" ? { error } : {}),
    debug: {
      mode: task.mode,
      ...(task.query ? { query: task.query } : {}),
      ...(task.source_keyword_id !== undefined ? { source_keyword_id: task.source_keyword_id } : {}),
      terminal_evidence: complete
        ? (explicitEmpty && !allCollectionsTerminal
          ? "explicit_empty"
          : "all_collections_terminal")
        : schemaDegraded
          ? "response_schema_degraded"
        : affirmativeEmpty
          ? "explicit_empty"
          : rows.length
            ? "bounded_nonterminal_page"
            : "response_unobserved",
      cursor_observed: Boolean(cursor),
      response_observed: responseObserved,
      authenticated_topic_observed: envelopes.some(envelope =>
        envelope.route === task.mode && envelope.collection_id === "topic:xdt_fbsearch__top_serp_graphql"),
      collection_count: summaries.length,
      terminal_collection_count: terminalCollections,
      observed_count: observedCount,
      rejected_count: rejectedCount,
      schema_degraded: schemaDegraded,
    },
  };
}

export async function executeInstagramDiscover(
  task: InstagramDiscoverTask,
): Promise<InstagramTaskResult> {
  const initialFailure = currentDiscoverPageFailure();
  if (initialFailure) return executorFailure(task, initialFailure);
  if (!discoverRouteMatches(task)) {
    return {
      task_id: task.id,
      claim_token: task.claim_token,
      status: "failed",
      items: [],
      scope_counts: { discover: 0 },
      scope_complete: { discover: false },
      error: "unexpected_discover_route",
    };
  }
  clearInstagramResponseBuffer();
  requestInstagramResponseReplay();
  const maxItems = integer(task.max_items, 30, 300);
  const maxPages = integer(task.max_pages, 3, 20);
  const requestIntervalMs = integer(task.request_interval_ms, 3_000, 30_000);
  for (let page = 1; page <= maxPages; page += 1) {
    await sleep(page === 1 ? 1_500 : Math.max(1_000, requestIntervalMs));
    if (page === 1) {
      // The page cap limits pagination, not first-render latency. Passive
      // hydration may take longer than 1.5s even after document complete.
      // Do not scroll/request another page or renew the durable idle deadline.
      const readyDeadline = Date.now() + 20_000;
      while (
        Date.now() < readyDeadline
        && discoverRouteMatches(task)
        && !currentDiscoverPageFailure()
        && !explicitDiscoverEmpty()
        && !readInstagramResponseBuffer().some(envelope =>
          envelope.route === task.mode || envelope.route === "unknown")
      ) {
        await sleep(500);
        requestInstagramResponseReplay();
      }
      const readinessFailure = currentDiscoverPageFailure();
      if (readinessFailure) {
        return buildInstagramDiscoverResult(task, readInstagramResponseBuffer(), false, readinessFailure);
      }
      if (!discoverRouteMatches(task)) return executorFailure(task, "unexpected_discover_route");
    }
    const envelopes = readInstagramResponseBuffer().filter(
      (envelope) => envelope.route === task.mode || envelope.route === "unknown",
    );
    const pageFailure = currentDiscoverPageFailure();
    const snapshot = buildInstagramDiscoverResult(task, envelopes, false, pageFailure);
    if (pageFailure || envelopes.some(envelope => envelope.error)) return snapshot;
    const cursor = [...envelopes].reverse().find((envelope) => envelope.cursor)?.cursor || "";
    const progressPersisted = await postProgress({
      task_id: task.id,
      claim_token: task.claim_token,
      phase: "discover",
      page,
      item_count: snapshot.items.length,
      items: snapshot.items,
      scope_counts: snapshot.scope_counts,
      scope_complete: snapshot.scope_complete,
      ...(cursor ? { cursor } : {}),
      accepted: envelopes.length > 0,
      debug: snapshot.debug,
    });
    if (!progressPersisted) break;
    if (snapshot.items.length >= maxItems || snapshot.scope_complete.discover === true) break;
    if (page === maxPages || envelopes.length === 0) break;
    window.scrollTo({ top: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight), behavior: "instant" });
    requestInstagramResponseReplay();
  }
  return buildInstagramDiscoverResult(
    task,
    readInstagramResponseBuffer(),
    explicitDiscoverEmpty(),
    currentDiscoverPageFailure(),
  );
}

export async function executeInstagramTask(task: InstagramTask): Promise<InstagramTaskResult> {
  return task.type === "bootstrap_events"
    ? executeInstagramBootstrap(task)
    : executeInstagramDiscover(task);
}

type InstagramTaskExecutor = (task: InstagramTask) => Promise<InstagramTaskResult>;

function executorFailure(task: InstagramTask, error: string): InstagramTaskResult {
  return {
    task_id: text(task?.id, 256),
    claim_token: text(task?.claim_token, 1024),
    status: "failed",
    items: [],
    scope_counts: {},
    scope_complete: task?.type === "bootstrap_events"
      ? Object.fromEntries(task.scopes.map((scope) => [scope, false]))
      : { discover: false },
    error,
    debug: { response_observed: false },
  };
}

/** Bind one task tab to one claim and collapse recovery resends onto one execution. */
export class InstagramTaskSingleflight {
  private boundKey: string | null = null;
  private pending: Promise<InstagramTaskResult> | null = null;
  private settled: InstagramTaskResult | null = null;

  run(
    task: InstagramTask,
    executor: InstagramTaskExecutor = executeInstagramTask,
  ): Promise<InstagramTaskResult> {
    const key = `${text(task?.id, 256)}\u0000${text(task?.claim_token, 1024)}`;
    if (!text(task?.id, 256) || !text(task?.claim_token, 1024)) {
      return Promise.resolve(executorFailure(task, "invalid_task_identity"));
    }
    if (this.boundKey !== null && this.boundKey !== key) {
      return Promise.resolve(executorFailure(task, "task_claim_conflict"));
    }
    this.boundKey = key;
    if (this.settled) return Promise.resolve(this.settled);
    if (this.pending) return this.pending;
    const execution = Promise.resolve()
      .then(() => executor(task))
      .catch(() => executorFailure(task, "executor_failed"));
    this.pending = execution;
    void execution.then((result) => {
      this.settled = result;
      this.pending = null;
    });
    return execution;
  }
}

const taskSingleflight = new InstagramTaskSingleflight();
let deliveryKey = "";
let deliveryPromise: Promise<void> | null = null;

function deliverInstagramTaskResult(task: InstagramTask): void {
  const key = `${text(task?.id, 256)}\u0000${text(task?.claim_token, 1024)}`;
  if (deliveryPromise && deliveryKey === key) return;
  deliveryKey = key;
  deliveryPromise = taskSingleflight.run(task).then(async (result) => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const response = await chrome.runtime.sendMessage({
          action: "INSTAGRAM_TASK_RESULT",
          data: result,
        }) as { ok?: boolean } | undefined;
        if (response?.ok === true) return;
      } catch {
        // Dispatcher recovery resends EXECUTE; the settled result is replayed.
      }
      if (attempt < 2) await sleep(250 * 2 ** attempt);
    }
    throw new Error("instagram_result_delivery_unacknowledged");
  }).finally(() => {
    deliveryPromise = null;
  });
  void deliveryPromise.catch(() => {});
}

export function installInstagramTaskMessageListener(): void {
  chrome.runtime.onMessage.addListener(
    (message: { action?: string; data?: InstagramTask }, _sender, sendResponse) => {
      if (message.action !== "INSTAGRAM_TASK_EXECUTE") return false;
      if (!isInstagramTaskTabLocation()) return false;
      const task = message.data as InstagramTask;
      deliverInstagramTaskResult(task);
      sendResponse({ ok: true });
      return true;
    },
  );
}

export function isKnownInstagramScope(value: unknown): value is InstagramBootstrapScope {
  return KNOWN_SCOPES.includes(value as InstagramBootstrapScope);
}
