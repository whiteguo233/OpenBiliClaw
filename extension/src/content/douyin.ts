/**
 * OpenBiliClaw — Douyin content script entry.
 *
 * Injected into douyin.com pages (isolated world). Listens for
 * DY_SCOPE_EXECUTE messages from the dispatcher, drives the per-scope
 * scrape — installs the MAIN-world fetch-tap (if not already), waits
 * for it to capture aweme JSON, programmatically scrolls to trigger
 * Douyin's virtual-list pagination, accumulates items into a
 * BootstrapItemSink, and posts DY_SCOPE_RESULT back when the scope is
 * exhausted (cap hit / round budget gone / consecutive stagnant
 * rounds).
 *
 * The MAIN-world fetch-tap is loaded separately via the
 * content_scripts MAIN-world entry in manifest.json
 * (dist/main/dy-fetch-tap.js, runs at document_start). That script
 * postMessages captured items here using a sentinel type
 * OPENBILICLAW_DOUYIN_AWEME_PAGE.
 *
 * Module isolation: zero imports from extension/src/content/xhs/.
 */

import type {
  DouyinBootstrapItem,
  DouyinScope,
  DouyinSearchItem,
  DouyinSearchScope,
} from "../main/dy-fetch-tap.js";
import { runtimeAssetCandidates } from "../shared/asset-prefix.ts";
import { douyinAdapter } from "../shared/platforms/douyin.ts";
import { registerE2EExecutor } from "./e2e-executor.ts";
import { installNativeSaveExecutor } from "./native-save/runtime.ts";
import { shouldStartPassiveCollector } from "./native-save/task-mode.ts";
import { saveDouyin, verifyDouyin } from "./native-save/douyin.ts";

const PASSIVE_DISCOVERY_REPLAY_LIMIT = 256;
const PASSIVE_DISCOVERY_REPLAY_TTL_MS = 120_000;
const PASSIVE_DISCOVERY_SCOPES = new Set<DouyinSearchScope>([
  "dy_search",
  "dy_hot",
  "dy_feed",
]);

interface PassiveDiscoveryReplayEntry {
  item: DouyinSearchItem;
  receivedAt: number;
}

/**
 * Preserve early MAIN-world discovery messages until a task executor attaches.
 *
 * The MAIN tap runs at document_start so it can observe Douyin's first feed
 * request. The isolated task listener is intentionally attached later, after
 * the task tab is complete. Without a bounded replay buffer, the first response
 * can land in that gap and a healthy feed is misreported as empty.
 */
export class DouyinPassiveDiscoveryReplayBuffer {
  private readonly entries = new Map<string, PassiveDiscoveryReplayEntry>();
  private readonly responseTimes = new Map<DouyinSearchScope, number[]>();
  private readonly maxItems: number;
  private readonly ttlMs: number;
  private readonly now: () => number;

  constructor(
    maxItems: number = PASSIVE_DISCOVERY_REPLAY_LIMIT,
    ttlMs: number = PASSIVE_DISCOVERY_REPLAY_TTL_MS,
    now: () => number = Date.now,
  ) {
    this.maxItems = maxItems;
    this.ttlMs = ttlMs;
    this.now = now;
  }

  ingest(scopeValue: unknown, values: unknown): number {
    const scope = normalizePassiveDiscoveryScope(scopeValue);
    if (!scope || !Array.isArray(values)) return 0;
    const receivedAt = this.now();
    this.prune(receivedAt);
    const responseTimes = this.responseTimes.get(scope) ?? [];
    responseTimes.push(receivedAt);
    this.responseTimes.set(scope, responseTimes.slice(-this.maxResponseCount()));

    let added = 0;
    for (const value of values) {
      const key = passiveDiscoveryItemKey(value);
      if (!key) continue;
      if (!key.startsWith(`${scope}:`)) continue;
      if (!this.entries.has(key)) added += 1;
      this.entries.delete(key);
      this.entries.set(key, {
        item: value as DouyinSearchItem,
        receivedAt,
      });
    }
    const limit = Math.max(1, Math.floor(this.maxItems));
    while (this.entries.size > limit) {
      const oldest = this.entries.keys().next().value as string | undefined;
      if (!oldest) break;
      this.entries.delete(oldest);
    }
    return added;
  }

  drain(scope: DouyinSearchScope): {
    items: DouyinSearchItem[];
    responsesObserved: number;
  } {
    this.prune(this.now());
    const items: DouyinSearchItem[] = [];
    for (const [key, entry] of this.entries) {
      if (!key.startsWith(`${scope}:`)) continue;
      items.push(entry.item);
      this.entries.delete(key);
    }
    const responsesObserved = this.responseTimes.get(scope)?.length ?? 0;
    this.responseTimes.delete(scope);
    return { items, responsesObserved };
  }

  private prune(now: number): void {
    const ttlMs = Math.max(0, this.ttlMs);
    for (const [key, entry] of this.entries) {
      if (now - entry.receivedAt <= ttlMs) continue;
      this.entries.delete(key);
    }
    for (const [scope, responseTimes] of this.responseTimes) {
      const fresh = responseTimes.filter((receivedAt) => now - receivedAt <= ttlMs);
      if (fresh.length > 0) {
        this.responseTimes.set(scope, fresh);
      } else {
        this.responseTimes.delete(scope);
      }
    }
  }

  private maxResponseCount(): number {
    return Math.max(1, Math.min(64, Math.floor(this.maxItems)));
  }
}

function normalizePassiveDiscoveryScope(value: unknown): DouyinSearchScope | null {
  const scope = String(value ?? "").trim() as DouyinSearchScope;
  return PASSIVE_DISCOVERY_SCOPES.has(scope) ? scope : null;
}

function passiveDiscoveryItemKey(value: unknown): string {
  if (!value || typeof value !== "object") return "";
  const item = value as Partial<DouyinSearchItem>;
  const scope = normalizePassiveDiscoveryScope(item.scope);
  if (!scope) return "";
  const awemeId = String(item.aweme_id ?? "").trim();
  return awemeId ? `${scope}:${awemeId}` : "";
}

const passiveDiscoveryReplayBuffer = new DouyinPassiveDiscoveryReplayBuffer();
const activePassiveDiscoveryScopes = new Set<DouyinSearchScope>();

function cachePassiveDiscoveryMessage(event: MessageEvent): void {
  if (!isSameWindowSameOriginDouyinMessage(event, window)) return;
  const data = event.data as Record<string, unknown> | null;
  if (!data || typeof data !== "object") return;
  if (data.type !== "OPENBILICLAW_DOUYIN_SEARCH_PAGE") return;
  const values = Array.isArray(data.items) ? data.items : [];
  const scope =
    normalizePassiveDiscoveryScope(data.scope) ??
    normalizePassiveDiscoveryScope(
      (values.find((item) => item && typeof item === "object") as
        | Partial<DouyinSearchItem>
        | undefined)?.scope,
    );
  if (!scope || activePassiveDiscoveryScopes.has(scope)) return;
  passiveDiscoveryReplayBuffer.ingest(scope, values);
}

if (typeof window !== "undefined") {
  window.addEventListener("message", cachePassiveDiscoveryMessage);
}

let behaviorCollectorStarted = false;

function startDouyinBehaviorCollector(): void {
  if (behaviorCollectorStarted) return;
  if (typeof window === "undefined" || typeof document === "undefined") return;
  if (typeof chrome === "undefined" || !chrome.runtime?.sendMessage) return;

  const start = (): void => {
    if (behaviorCollectorStarted) return;
    behaviorCollectorStarted = true;
    void shouldStartPassiveCollector().then((shouldStart) => {
      if (!shouldStart) return;
      void import("./kernel.js").then(({ startCollector }) => {
        startCollector(douyinAdapter);
      });
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
    return;
  }
  start();
}

startDouyinBehaviorCollector();
registerE2EExecutor("douyin");
if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
  installNativeSaveExecutor("douyin", saveDouyin, verifyDouyin);
}

/**
 * Re-inject the MAIN-world fetch-tap by appending a <script> element
 * with src pointing at the extension's bundled dy-fetch-tap.js.
 *
 * Why this is needed: chrome.scripting.executeScript runs once at
 * page load. After each click-driven SPA route, Douyin's React app
 * may re-set window.fetch with its own wrapper — replacing our
 * wrap and silently breaking aweme capture (e2e probe 2026-05-08:
 * install_messages_received=3 but aweme_messages_received=0
 * across all 4 scopes). Re-injecting the fetch-tap script after
 * every nav guarantees we're wrapping the latest live fetch.
 *
 * The script element is inserted into documentElement (DOM is
 * shared between isolated and MAIN worlds) and removed after
 * onload to keep the DOM clean. dy-fetch-tap.js is in
 * web_accessible_resources so chrome.runtime.getURL resolves it.
 */
function reinjectFetchTap(): void {
  if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.getURL) return;
  const candidates = runtimeAssetCandidates("main/dy-fetch-tap.js");
  const injectCandidate = (index: number): void => {
    const file = candidates[index];
    if (!file) return;
    const script = document.createElement("script");
    script.src = chrome.runtime.getURL(file);
    script.onload = () => script.remove();
    script.onerror = () => {
      script.remove();
      injectCandidate(index + 1);
    };
    (document.head || document.documentElement).appendChild(script);
  };
  injectCandidate(0);
}

// Dynamic import for the chrome-lifecycle code path so node:test's
// --experimental-strip-types resolver doesn't have to chase the
// `.js → .ts` chain at module-load time. Pure helpers exported from
// this file (isValidScopeExecuteMessage) stay synchronously
// importable for unit tests. esbuild inlines the dynamic import at
// build time, so production runtime sees no extra latency.
async function loadTaskExecutorHelpers(): Promise<{
  BootstrapItemSink: typeof import("./dy/task-executor.js").BootstrapItemSink;
  dyShouldContinueScroll: typeof import("./dy/task-executor.js").dyShouldContinueScroll;
  ingestMainWorldFetchMessage: typeof import("./dy/task-executor.js").ingestMainWorldFetchMessage;
}> {
  return await import("./dy/task-executor.js");
}

async function loadDomExtractor(): Promise<{
  extractDouyinItemsFromDocument: typeof import("./dy/dom-extractor.js").extractDouyinItemsFromDocument;
  extractDouyinSearchItemsFromDocument: typeof import("./dy/dom-extractor.js").extractDouyinSearchItemsFromDocument;
  pickSearchScrollTarget: typeof import("./dy/dom-extractor.js").pickSearchScrollTarget;
}> {
  return await import("./dy/dom-extractor.js");
}

async function loadBootstrapHelpers(): Promise<{
  extractDouyinSecUidFromRenderData: typeof import("./dy/bootstrap.js").extractDouyinSecUidFromRenderData;
  reconcileDouyinSelfIdentity: typeof import("./dy/bootstrap.js").reconcileDouyinSelfIdentity;
}> {
  return await import("./dy/bootstrap.js");
}

interface ScopeExecuteMessage {
  task_id: string;
  scope: DouyinScope;
  max_items_per_scope: number;
  max_scroll_rounds: number;
  max_stagnant_scroll_rounds: number;
  debug_inject_status?: string;
}

interface ScopeResultPayload {
  task_id: string;
  scope: DouyinScope;
  items: DouyinBootstrapItem[];
  scope_count: number;
  status: "ok" | "empty" | "degraded" | "failed";
  error?: string;
  /**
   * Diagnostic counters surfaced through the dispatcher into the
   * /api/sources/dy/task-result partial debug field. Lets us
   * disambiguate "scope returned empty because fetch-tap never
   * installed" from "fetch-tap installed but Douyin returned empty
   * 200s (risk control)" without needing the user's browser console.
   */
  debug?: {
    fetch_tap_install_status: "unknown" | "installed" | "skipped_no_sdk";
    aweme_messages_received: number;
    install_messages_received: number;
    dom_items_harvested?: number;
    api_items_harvested?: number;
    api_pages_fetched?: number;
    api_error?: string;
    sec_uid?: string;
    sec_uid_source?: DouyinSecUidSource;
    identity_error?: string;
    end_of_feed?: string;
    inject_status?: string;
    page_url?: string;
    profile_link_found?: boolean;
    sub_tab_found?: boolean;
  };
}

interface SearchExecuteMessage {
  task_id: string;
  keyword: string;
  max_items: number;
  debug_inject_status?: string;
  /** Resume collection in the new document after the UI caused a full navigation. */
  resume_after_navigation?: boolean;
}

interface HotExecuteMessage {
  task_id: string;
  sentence_id: string;
  word: string;
  seed_aweme_id?: string;
  max_items: number;
  debug_inject_status?: string;
}

interface FeedExecuteMessage {
  task_id: string;
  max_items: number;
  debug_inject_status?: string;
}

interface SearchResultPayload {
  task_id: string;
  keyword: string;
  items: DouyinSearchItem[];
  scope_count: number;
  status: "ok" | "empty" | "failed";
  error?: string;
  debug?: {
    fetch_tap_install_status: "unknown" | "installed" | "skipped_no_sdk";
    api_pages_fetched: number;
    api_items_harvested: number;
    dom_items_harvested: number;
    api_error?: string;
    ui_triggered?: boolean;
    search_navigation_ok?: boolean;
    search_submit_method?: string;
    navigation_resumed?: boolean;
    passive_items_harvested?: number;
    passive_responses_observed?: number;
    early_buffer_items?: number;
    scroll_rounds?: number;
    inject_status?: string;
    page_url?: string;
  };
}

interface HotResultPayload {
  task_id: string;
  sentence_id: string;
  word: string;
  items: DouyinSearchItem[];
  scope_count: number;
  status: "ok" | "empty" | "failed";
  error?: string;
  debug?: {
    fetch_tap_install_status: "unknown" | "installed" | "skipped_no_sdk";
    api_pages_fetched: number;
    api_items_harvested: number;
    api_error?: string;
    dom_items_harvested?: number;
    passive_items_harvested?: number;
    passive_responses_observed?: number;
    early_buffer_items?: number;
    seed_aweme_id?: string;
    ui_triggered?: boolean;
    inject_status?: string;
    page_url?: string;
  };
}

interface FeedResultPayload {
  task_id: string;
  items: DouyinSearchItem[];
  scope_count: number;
  status: "ok" | "empty" | "failed";
  error?: string;
  debug?: {
    fetch_tap_install_status: "unknown" | "installed" | "skipped_no_sdk";
    api_pages_fetched: number;
    api_items_harvested: number;
    dom_items_harvested: number;
    passive_items_harvested?: number;
    passive_responses_observed?: number;
    early_buffer_items?: number;
    api_error?: string;
    inject_status?: string;
    page_url?: string;
  };
}

const SCROLL_DELAY_MS = 1_500;
const POST_INSTALL_SETTLE_MS = 800;

/**
 * Accept only messages emitted by this page's top-level Window and origin.
 *
 * This is defense-in-depth against accidental cross-frame/page chatter, not
 * an authorization boundary: any script running in the same page can still
 * call window.postMessage. Sentinels, request IDs, and payload validation
 * remain required for every bridge message.
 */
export function isSameWindowSameOriginDouyinMessage(
  event: MessageEvent,
  target: Window,
): boolean {
  return event.source === target && event.origin === target.location.origin;
}

// Module-level: track the last fetch-tap install ping. The MAIN-world
// dy-fetch-tap.js posts one of:
//   { type: "OPENBILICLAW_DOUYIN_FETCH_TAP_INSTALL", status: "installed" }
//   { type: "OPENBILICLAW_DOUYIN_FETCH_TAP_INSTALL", status: "skipped_no_sdk" }
// at install resolve. We capture it here so runScope can include the
// status in the result payload's debug field — that's how dispatcher
// diagnostic logs see whether the MAIN-world script actually wrapped
// fetch in this tab.
let _lastFetchTapInstallStatus: "unknown" | "installed" | "skipped_no_sdk" = "unknown";
let _installMessagesReceived = 0;
type DouyinSecUidSource = "" | "profile_self";
let _detectedSecUid = "";
let _detectedSecUidSource: DouyinSecUidSource = "";

function rememberAuthoritativeDouyinSecUid(secUid: string): void {
  const normalized = secUid.trim();
  if (!normalized) return;
  _detectedSecUid = normalized;
  _detectedSecUidSource = "profile_self";
}

if (typeof window !== "undefined") {
  window.addEventListener("message", (event: MessageEvent) => {
    if (!isSameWindowSameOriginDouyinMessage(event, window)) return;
    const data = event?.data as { type?: unknown; status?: unknown } | null;
    if (!data || typeof data !== "object") return;
    if (data.type === "OPENBILICLAW_DOUYIN_FETCH_TAP_INSTALL") {
      _installMessagesReceived += 1;
      const s = String(data.status ?? "");
      if (s === "installed" || s === "skipped_no_sdk") {
        _lastFetchTapInstallStatus = s;
      }
      return;
    }
  });
}

/**
 * Drive the MAIN-world API harvester for the given scope. Returns
 * the items it crawled (or [] on timeout/error). The MAIN-world tap
 * was installed at page load and is listening for
 * OPENBILICLAW_DOUYIN_API_REQUEST messages — see dy-fetch-tap.ts.
 *
 * Per-call timeout is generous: 50 pages × ~500ms = 25s, plus signing
 * overhead and risk-control rate limits, so a 90s ceiling lets even
 * the largest user's likes/favorites finish.
 */
async function harvestScopeViaApiBridge(
  scope: DouyinScope,
  secUid: string,
  maxItems: number,
  timeoutMs: number = 90_000,
): Promise<{ items: DouyinBootstrapItem[]; pages: number; error?: string }> {
  return new Promise((resolve) => {
    const requestId = `obc_dy_api_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      window.removeEventListener("message", onMessage);
      resolve({ items: [], pages: 0, error: "timeout" });
    }, timeoutMs);
    const onMessage = (event: MessageEvent): void => {
      if (!isSameWindowSameOriginDouyinMessage(event, window)) return;
      const data = event?.data as Record<string, unknown> | null;
      if (!data || typeof data !== "object") return;
      if (data.type !== "OPENBILICLAW_DOUYIN_API_RESPONSE") return;
      if (data.requestId !== requestId) return;
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      const items = Array.isArray(data.items)
        ? (data.items as DouyinBootstrapItem[])
        : [];
      const pages = Number(data.pages_fetched ?? 0);
      const error = typeof data.error === "string" ? data.error : undefined;
      resolve({ items, pages, error });
    };
    window.addEventListener("message", onMessage);
    window.postMessage(
      {
        type: "OPENBILICLAW_DOUYIN_API_REQUEST",
        requestId,
        scope,
        secUid,
        maxItems,
      },
      window.location.origin,
    );
  });
}

async function resolveDouyinSelfSecUidViaBridge(
  timeoutMs: number = 15_000,
): Promise<{ secUid: string; error?: string }> {
  return new Promise((resolve) => {
    const requestId = `obc_dy_identity_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      window.removeEventListener("message", onMessage);
      resolve({ secUid: "", error: "timeout" });
    }, timeoutMs);
    const onMessage = (event: MessageEvent): void => {
      if (!isSameWindowSameOriginDouyinMessage(event, window)) return;
      const data = event?.data as Record<string, unknown> | null;
      if (!data || typeof data !== "object") return;
      if (data.type !== "OPENBILICLAW_DOUYIN_IDENTITY_RESPONSE") return;
      if (data.requestId !== requestId || settled) return;
      settled = true;
      clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      const secUid = typeof data.secUid === "string" ? data.secUid.trim() : "";
      const error = typeof data.error === "string" ? data.error : undefined;
      resolve({ secUid, error });
    };
    window.addEventListener("message", onMessage);
    window.postMessage(
      {
        type: "OPENBILICLAW_DOUYIN_IDENTITY_REQUEST",
        requestId,
      },
      window.location.origin,
    );
  });
}

async function resolveDouyinBootstrapSecUid(): Promise<{
  secUid: string;
  source: DouyinSecUidSource;
  error?: string;
}> {
  if (_detectedSecUid && _detectedSecUidSource === "profile_self") {
    return { secUid: _detectedSecUid, source: _detectedSecUidSource };
  }
  let renderDataSecUid = "";
  let reconcileDouyinSelfIdentity:
    | typeof import("./dy/bootstrap.js").reconcileDouyinSelfIdentity
    | undefined;
  try {
    const helpers = await loadBootstrapHelpers();
    reconcileDouyinSelfIdentity = helpers.reconcileDouyinSelfIdentity;
    const raw = document.getElementById("RENDER_DATA")?.textContent ?? "";
    renderDataSecUid = helpers.extractDouyinSecUidFromRenderData(raw);
  } catch {
    // RENDER_DATA is only a candidate; profile/self remains authoritative.
  }

  const profileResult = await resolveDouyinSelfSecUidViaBridge();
  const identity = reconcileDouyinSelfIdentity
    ? reconcileDouyinSelfIdentity({
        renderDataSecUid,
        profileSelfSecUid: profileResult.secUid,
        profileError: profileResult.error,
      })
    : {
        secUid: profileResult.secUid,
        source: profileResult.secUid ? ("profile_self" as const) : ("" as const),
        conflict: false,
        ...(!profileResult.secUid
          ? { error: profileResult.error ?? "identity_unavailable" }
          : {}),
      };
  if (identity.secUid) {
    // Cache only the identity positively confirmed by profile/self.
    rememberAuthoritativeDouyinSecUid(identity.secUid);
    return { secUid: _detectedSecUid, source: _detectedSecUidSource };
  }

  return {
    secUid: "",
    source: "",
    error: identity.error ?? "identity_unavailable",
  };
}

async function harvestSearchViaApiBridge(
  keyword: string,
  maxItems: number,
  timeoutMs: number = 20_000,
): Promise<{ items: DouyinSearchItem[]; pages: number; error?: string }> {
  return new Promise((resolve) => {
    const requestId = `obc_dy_search_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      window.removeEventListener("message", onMessage);
      resolve({ items: [], pages: 0, error: "timeout" });
    }, timeoutMs);
    const onMessage = (event: MessageEvent): void => {
      if (!isSameWindowSameOriginDouyinMessage(event, window)) return;
      const data = event?.data as Record<string, unknown> | null;
      if (!data || typeof data !== "object") return;
      if (data.type !== "OPENBILICLAW_DOUYIN_SEARCH_API_RESPONSE") return;
      if (data.requestId !== requestId) return;
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      const items = Array.isArray(data.items) ? (data.items as DouyinSearchItem[]) : [];
      const pages = Number(data.pages_fetched ?? 0);
      const error = typeof data.error === "string" ? data.error : undefined;
      resolve({ items, pages, error });
    };
    window.addEventListener("message", onMessage);
    window.postMessage(
      {
        type: "OPENBILICLAW_DOUYIN_SEARCH_API_REQUEST",
        requestId,
        keyword,
        maxItems,
      },
      window.location.origin,
    );
  });
}

async function harvestHotRelatedViaApiBridge(
  seedAwemeId: string,
  maxItems: number,
  sentenceId: string,
  word: string,
  timeoutMs: number = 45_000,
): Promise<{ items: DouyinSearchItem[]; pages: number; error?: string }> {
  return new Promise((resolve) => {
    const requestId = `obc_dy_hot_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      window.removeEventListener("message", onMessage);
      resolve({ items: [], pages: 0, error: "timeout" });
    }, timeoutMs);
    const onMessage = (event: MessageEvent): void => {
      if (!isSameWindowSameOriginDouyinMessage(event, window)) return;
      const data = event?.data as Record<string, unknown> | null;
      if (!data || typeof data !== "object") return;
      if (data.type !== "OPENBILICLAW_DOUYIN_HOT_API_RESPONSE") return;
      if (data.requestId !== requestId) return;
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      const items = Array.isArray(data.items) ? (data.items as DouyinSearchItem[]) : [];
      const pages = Number(data.pages_fetched ?? 0);
      const error = typeof data.error === "string" ? data.error : undefined;
      resolve({ items, pages, error });
    };
    window.addEventListener("message", onMessage);
    window.postMessage(
      {
        type: "OPENBILICLAW_DOUYIN_HOT_API_REQUEST",
        requestId,
        seedAwemeId,
        maxItems,
        sentenceId,
        word,
      },
      window.location.origin,
    );
  });
}

async function harvestFeedViaApiBridge(
  maxItems: number,
  timeoutMs: number = 45_000,
): Promise<{ items: DouyinSearchItem[]; pages: number; error?: string }> {
  return new Promise((resolve) => {
    const requestId = `obc_dy_feed_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      window.removeEventListener("message", onMessage);
      resolve({ items: [], pages: 0, error: "timeout" });
    }, timeoutMs);
    const onMessage = (event: MessageEvent): void => {
      if (!isSameWindowSameOriginDouyinMessage(event, window)) return;
      const data = event?.data as Record<string, unknown> | null;
      if (!data || typeof data !== "object") return;
      if (data.type !== "OPENBILICLAW_DOUYIN_FEED_API_RESPONSE") return;
      if (data.requestId !== requestId) return;
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      const items = Array.isArray(data.items) ? (data.items as DouyinSearchItem[]) : [];
      const pages = Number(data.pages_fetched ?? 0);
      const error = typeof data.error === "string" ? data.error : undefined;
      resolve({ items, pages, error });
    };
    window.addEventListener("message", onMessage);
    window.postMessage(
      {
        type: "OPENBILICLAW_DOUYIN_FEED_API_REQUEST",
        requestId,
        maxItems,
      },
      window.location.origin,
    );
  });
}

function extractAwemeIdFromLocationHref(href: string): string {
  const match = href.match(/\/video\/(\d+)/);
  return match?.[1] ?? "";
}

async function waitForCurrentVideoAwemeId(timeoutMs: number = 8_000): Promise<string> {
  for (let waited = 0; waited <= timeoutMs; waited += 200) {
    const awemeId = extractAwemeIdFromLocationHref(location.href);
    if (awemeId) return awemeId;
    await sleep(200);
  }
  return "";
}

function dedupeSearchItems(items: DouyinSearchItem[], maxItems: number): DouyinSearchItem[] {
  const cap = Math.max(0, Math.floor(maxItems));
  const indexByKey = new Map<string, number>();
  const result: DouyinSearchItem[] = [];
  for (const item of items) {
    const key = item.aweme_id || `${item.title}:${item.author}`;
    if (!key) continue;
    const existingIndex = indexByKey.get(key);
    if (existingIndex !== undefined) {
      result[existingIndex] = mergeSearchItemMetadata(result[existingIndex]!, item);
      continue;
    }
    if (result.length >= cap) continue;
    indexByKey.set(key, result.length);
    result.push(item);
  }
  return result;
}

function mergeSearchItemMetadata(
  primary: DouyinSearchItem,
  fallback: DouyinSearchItem,
): DouyinSearchItem {
  return {
    ...primary,
    hot_word: primary.hot_word || fallback.hot_word,
    sentence_id: primary.sentence_id || fallback.sentence_id,
    seed_aweme_id: primary.seed_aweme_id || fallback.seed_aweme_id,
    view_count: primary.view_count ?? fallback.view_count,
    like_count: primary.like_count ?? fallback.like_count,
    collect_count: primary.collect_count ?? fallback.collect_count,
    comment_count: primary.comment_count ?? fallback.comment_count,
    share_count: primary.share_count ?? fallback.share_count,
  };
}

export function filterDiscoveryItemsForScope(
  items: DouyinSearchItem[],
  scope: DouyinSearchScope,
  maxItems: number,
): DouyinSearchItem[] {
  return dedupeSearchItems(
    items.filter((item) => item.scope === scope),
    maxItems,
  );
}

export function douyinDiscoveryExecutionPolicy(): {
  search: { activeApiBridge: true; passiveFetchTap: true; domInteraction: true };
  hot: { activeApiBridge: true; passiveFetchTap: true; domInteraction: true };
  feed: { activeApiBridge: false; passiveFetchTap: true; domInteraction: true };
} {
  return {
    search: { activeApiBridge: true, passiveFetchTap: true, domInteraction: true },
    hot: { activeApiBridge: true, passiveFetchTap: true, domInteraction: true },
    feed: { activeApiBridge: false, passiveFetchTap: true, domInteraction: true },
  };
}

export function classifyDouyinDiscoveryCompletion(input: {
  source: "search" | "hot" | "feed";
  itemCount: number;
  injectStatus?: string;
  fetchTapInstallStatus: "unknown" | "installed" | "skipped_no_sdk";
  apiError?: string;
  uiTriggered?: boolean;
  searchNavigationOk?: boolean;
  alternateCollectionCompleted?: boolean;
  passiveResponsesObserved?: number;
  domItemsHarvested?: number;
}): { status: "ok" | "empty" | "failed"; error?: string } {
  if (input.itemCount > 0) return { status: "ok" };

  const injectStatus = String(input.injectStatus ?? "").trim().toLowerCase();
  if (injectStatus === "scripting_api_missing" || /^error(?:\s*:|$)/.test(injectStatus)) {
    return { status: "failed", error: "fetch_tap_injection_failed" };
  }

  if (input.source === "search") {
    if (input.uiTriggered === false) {
      return { status: "failed", error: "search_ui_not_triggered" };
    }
    if (input.searchNavigationOk === false) {
      return { status: "failed", error: "search_navigation_failed" };
    }
  }
  if (input.source === "hot" && input.uiTriggered === false) {
    return { status: "failed", error: "hot_ui_not_triggered" };
  }

  const apiError = String(input.apiError ?? "").trim().toLowerCase();
  if (apiError) {
    if (/\b429\b|rate[\s_-]*limit|too many requests|too frequent|hit_shark|请求频繁/.test(apiError)) {
      return { status: "failed", error: "api_rate_limited" };
    }
    if (/\btimeout\b|timed[\s_-]*out|\babort(?:ed|error)?\b/.test(apiError)) {
      return { status: "failed", error: "api_timeout" };
    }
    if (
      /\b(?:http(?:\s+status)?|status(?:\s+code)?)\s*[:=_-]?\s*[45]\d{2}\b/.test(apiError)
    ) {
      return { status: "failed", error: "api_http_error" };
    }
    return { status: "failed", error: "api_collection_failed" };
  }

  if (input.fetchTapInstallStatus === "skipped_no_sdk") {
    return { status: "failed", error: "fetch_tap_sdk_unavailable" };
  }
  const feedObservationReported = input.passiveResponsesObserved !== undefined;
  if (
    input.source === "feed" &&
    input.fetchTapInstallStatus === "installed" &&
    feedObservationReported &&
    Number(input.passiveResponsesObserved ?? 0) <= 0
  ) {
    return { status: "failed", error: "feed_no_observed_response" };
  }
  if (
    input.fetchTapInstallStatus === "installed" ||
    input.alternateCollectionCompleted === true
  ) {
    return { status: "empty" };
  }
  return { status: "failed", error: "fetch_tap_status_unknown" };
}

interface PassiveDiscoveryCollector {
  detach: () => void;
  /** How many items arrived passively (page-issued responses via fetch-tap). */
  passiveCount: () => number;
  /** How many matching page responses were observed, including valid empty responses. */
  responseCount: () => number;
  /** How many unique items were replayed from before task-listener attachment. */
  earlyBufferCount: () => number;
}

export function shouldReplayEarlyDiscoveryItems(
  scope: DouyinSearchScope,
  resumeAfterNavigation: boolean,
): boolean {
  return scope !== "dy_search" || resumeAfterNavigation;
}

function attachPassiveDiscoveryCollector(
  allItems: DouyinSearchItem[],
  scope: DouyinSearchScope,
  replayEarly: boolean = true,
): PassiveDiscoveryCollector {
  const drained = passiveDiscoveryReplayBuffer.drain(scope);
  // A normal search execution attaches before submitting its keyword. Any
  // buffered dy_search rows therefore belong to the previous keyword and
  // must be discarded. A full-navigation resume runs in a fresh document and
  // does need the early response captured before the resumed collector.
  const early = shouldReplayEarlyDiscoveryItems(scope, replayEarly)
    ? drained
    : { items: [] as DouyinSearchItem[], responsesObserved: 0 };
  allItems.push(...early.items);
  activePassiveDiscoveryScopes.add(scope);
  let passiveCount = early.items.length;
  let responseCount = early.responsesObserved;
  const onMessage = (event: MessageEvent): void => {
    if (!isSameWindowSameOriginDouyinMessage(event, window)) return;
    const data = event?.data as Record<string, unknown> | null;
    if (!data || typeof data !== "object") return;
    if (data.type !== "OPENBILICLAW_DOUYIN_SEARCH_PAGE") return;
    if (!Array.isArray(data.items)) return;
    const messageScope =
      normalizePassiveDiscoveryScope(data.scope) ??
      normalizePassiveDiscoveryScope(
        (data.items.find((item) => item && typeof item === "object") as
          | Partial<DouyinSearchItem>
          | undefined)?.scope,
      );
    if (messageScope !== scope) return;
    responseCount += 1;
    const matchingItems = (data.items as DouyinSearchItem[]).filter(
      (item) => item.scope === scope,
    );
    passiveCount += matchingItems.length;
    allItems.push(...matchingItems);
  };
  window.addEventListener("message", onMessage);
  return {
    detach: () => {
      window.removeEventListener("message", onMessage);
      activePassiveDiscoveryScopes.delete(scope);
    },
    passiveCount: () => passiveCount,
    responseCount: () => responseCount,
    earlyBufferCount: () => early.items.length,
  };
}

/**
 * Pure round-budget controller for the adaptive search scroll loop.
 *
 * Call `shouldContinue(count)` with the current (deduped) item count
 * before each round. It stops when: the count reached `maxItems`, the
 * round cap was hit, or `stagnantLimit` consecutive rounds ended
 * without the count growing.
 */
export function createScrollRoundController(opts: {
  roundCap: number;
  stagnantLimit: number;
  maxItems: number;
}): { shouldContinue(count: number): boolean; roundsExecuted(): number } {
  let rounds = 0;
  let stagnantRounds = 0;
  let lastCount: number | null = null;
  return {
    shouldContinue(count: number): boolean {
      if (lastCount !== null) {
        if (count <= lastCount) stagnantRounds += 1;
        else stagnantRounds = 0;
      }
      lastCount = count;
      if (count >= opts.maxItems) return false;
      if (stagnantRounds >= opts.stagnantLimit) return false;
      if (rounds >= opts.roundCap) return false;
      rounds += 1;
      return true;
    },
    roundsExecuted: () => rounds,
  };
}

export function isDouyinSearchResultUrl(href: string, keyword?: string): boolean {
  try {
    const url = new URL(href, "https://www.douyin.com");
    const path = decodeURIComponent(url.pathname);
    const segments = path.split("/").filter(Boolean);
    const searchIndex = segments.lastIndexOf("search");
    if (searchIndex < 0) return false;
    const trimmedKeyword = String(keyword ?? "").trim();
    if (!trimmedKeyword) return true;
    return (
      (segments[searchIndex + 1] ?? "") === trimmedKeyword ||
      url.searchParams.get("keyword") === trimmedKeyword ||
      url.searchParams.get("q") === trimmedKeyword
    );
  } catch {
    return false;
  }
}

async function waitForSearchResultNavigation(
  keyword: string,
  timeoutMs: number = 5_000,
): Promise<boolean> {
  for (let waited = 0; waited <= timeoutMs; waited += 200) {
    if (isDouyinSearchResultUrl(location.href, keyword)) return true;
    await sleep(200);
  }
  return isDouyinSearchResultUrl(location.href, keyword);
}

interface SearchUiTriggerResult {
  submitted: boolean;
  navigated: boolean;
  method: "button" | "enter" | "none";
}

async function triggerSearchUi(keyword: string): Promise<SearchUiTriggerResult> {
  let input: HTMLInputElement | HTMLTextAreaElement | null = null;
  for (let waited = 0; waited < 5_000 && !input; waited += 200) {
    const inputs = Array.from(
      document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input, textarea"),
    );
    input =
      inputs.find((el) => (el.getAttribute("placeholder") ?? "").includes("搜索")) ??
      inputs[0] ??
      null;
    if (!input) await sleep(200);
  }
  if (!input) return { submitted: false, navigated: false, method: "none" };
  input.focus();
  const proto =
    input instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) {
    setter.call(input, keyword);
  } else {
    input.value = keyword;
  }
  input.dispatchEvent(
    new InputEvent("input", {
      bubbles: true,
      inputType: "insertText",
      data: keyword,
    }),
  );
  input.dispatchEvent(new Event("change", { bubbles: true }));

  const buttons = Array.from(document.querySelectorAll<HTMLElement>("button, [role='button']"));
  const button = buttons.find((el) => (el.textContent ?? "").trim().includes("搜索"));
  if (button) {
    fireRealClick(button);
    if (await waitForSearchResultNavigation(keyword)) {
      return { submitted: true, navigated: true, method: "button" };
    }
  }
  input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  input.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", bubbles: true }));
  return {
    submitted: true,
    navigated: await waitForSearchResultNavigation(keyword, 3_000),
    method: "enter",
  };
}

function visibleText(el: HTMLElement): string {
  return (el.textContent ?? "").replace(/\s+/g, " ").trim();
}

function findClickableByText(labels: string[]): HTMLElement | null {
  const normalized = labels.map((label) => label.trim()).filter(Boolean);
  if (!normalized.length) return null;
  const candidates = Array.from(
    document.querySelectorAll<HTMLElement>(
      'a, button, [role="link"], [role="button"], [role="tab"], [data-e2e]',
    ),
  );
  for (const el of candidates) {
    const text = visibleText(el);
    if (!text) continue;
    if (normalized.some((label) => text === label || text.includes(label))) return el;
  }
  return null;
}

function findHotTarget(sentenceId: string, word: string): HTMLElement | null {
  const safeSentenceId = sentenceId.trim();
  if (safeSentenceId) {
    const hrefTarget = Array.from(document.querySelectorAll<HTMLAnchorElement>("a[href]")).find((anchor) => {
      const href = anchor.href || anchor.getAttribute("href") || "";
      return href.includes(`/hot/${safeSentenceId}`) || href.includes(`sentence_id=${safeSentenceId}`);
    });
    if (hrefTarget) return hrefTarget;
  }
  const byWord = findClickableByText([word]);
  if (byWord) return byWord;
  return null;
}

async function triggerHotUi(sentenceId: string, word: string): Promise<boolean> {
  let target = findHotTarget(sentenceId, word);
  if (target) {
    fireRealClick(target);
    await sleep(2_500);
    return true;
  }

  const hotEntry = findClickableByText(["热点", "热榜", "热门", "抖音热榜"]);
  if (hotEntry) {
    fireRealClick(hotEntry);
    await sleep(2_500);
    target = findHotTarget(sentenceId, word);
    if (target) {
      fireRealClick(target);
      await sleep(2_500);
      return true;
    }
  }
  return false;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Find the homepage's "我" / profile link. Tries multiple selectors
 * because Douyin's UI structure shifts across releases.
 *
 * Returns the element to click, or null if no candidate found.
 */
function findProfileLink(): HTMLElement | null {
  // Most direct: anchor whose href points at /user/self or any
  // /user/<sec_uid> pattern. CRITICAL: skip anchors whose href
  // includes "?showTab=" — Douyin's leftnav has direct shortcuts
  // to "喜欢" / "收藏" / "关注" sub-tabs (e.g.
  // <a href="/user/self?showTab=like">), and matching one of those
  // sends us to the wrong tab. e2e probe 2026-05-08 caught this.
  const candidates = Array.from(
    document.querySelectorAll<HTMLAnchorElement>(
      'a[href="/user/self"], a[href^="/user/MS4w"], a[href*="/user/self"], a[href*="/user/"]',
    ),
  );
  for (const anchor of candidates) {
    const href = anchor.getAttribute("href") ?? "";
    if (href.includes("?showTab=")) continue; // skip sub-tab shortcuts
    return anchor;
  }
  // Data-attribute selectors (e2e test selectors Douyin sometimes ships).
  const dataSelectors = [
    '[data-e2e="profile-icon"]',
    '[data-e2e="user-tab-self"]',
    '[data-e2e="user-info"]',
    '[data-e2e="my-tab"]',
  ];
  for (const sel of dataSelectors) {
    const el = document.querySelector(sel);
    if (el && "click" in el) return el as HTMLElement;
  }
  // Last resort: anchor / button / clickable div whose visible text
  // is the leftnav profile entry. Douyin's left sidebar has been
  // observed shipping this as either "我" or "我的" (and "我的"
  // occasionally as part of a longer label like "我的关注"). Match
  // exactly to avoid clicking an unrelated string-containing element.
  const profileLabels = ["我", "我的", "个人主页"];
  const textCandidates = Array.from(
    document.querySelectorAll<HTMLElement>(
      'a, button, [role="link"], [role="button"], [data-e2e]',
    ),
  );
  for (const el of textCandidates) {
    const text = el.textContent?.trim() ?? "";
    if (profileLabels.includes(text)) return el;
  }
  return null;
}

/**
 * Find the sub-tab element on the user profile page for a given scope.
 * Returns null for dy_post (which is the default visible tab — no
 * click needed to land on it).
 */
function findScopeSubTab(scope: DouyinScope): HTMLElement | null {
  if (scope === "dy_post") return null;
  const dataSelectors: Record<DouyinScope, string[]> = {
    dy_post: [],
    dy_collect: [
      '[data-e2e="user-favorite-tab"]',
      '[data-e2e="user-tab-favorite_collection"]',
      'a[href*="favorite_collection"]',
    ],
    dy_like: [
      '[data-e2e="user-like-tab"]',
      '[data-e2e="user-tab-like"]',
      'a[href*="showTab=like"]',
    ],
    dy_follow: [
      '[data-e2e="user-following-tab"]',
      '[data-e2e="user-tab-following"]',
      'a[href*="showTab=following"]',
    ],
  };
  for (const sel of dataSelectors[scope]) {
    const el = document.querySelector(sel);
    if (el && "click" in el) return el as HTMLElement;
  }
  // Text fallback. Douyin sub-tab labels:
  //   dy_collect → 收藏
  //   dy_like    → 喜欢
  //   dy_follow  → 关注
  const labelMap: Record<DouyinScope, string> = {
    dy_post: "作品",
    dy_collect: "收藏",
    dy_like: "喜欢",
    dy_follow: "关注",
  };
  const label = labelMap[scope];
  const candidates = Array.from(
    document.querySelectorAll<HTMLElement>('a, button, [role="tab"], [class*="tab"]'),
  );
  for (const el of candidates) {
    if (el.textContent?.trim() === label) return el;
  }
  return null;
}

/**
 * Drive the page from wherever it currently is to the requested
 * scope's view, using **clicks**, not URL writes. This makes the
 * navigation look like user behaviour to Douyin's risk control —
 * direct chrome.tabs.update jumps to /user/self trip the captcha
 * intermediate page (verified 2026-05-08 e2e).
 *
 * Flow per scope:
 *   1. If we're on the homepage (anywhere outside /user/), click
 *      the profile link to land on /user/<sec_uid>. SPA-route, no
 *      document commit, fetch-tap stays.
 *   2. If the requested scope isn't dy_post (which is the default
 *      visible tab), click the sub-tab element. Again SPA-route.
 *
 * Returns true on best-effort success (click(s) attempted), false if
 * we couldn't find any candidate elements — caller can still proceed
 * to scroll loop, just won't have items.
 */
interface ClickToScopeReport {
  page_url: string;
  profile_link_found: boolean;
  sub_tab_found: boolean;
}

async function clickToScope(scope: DouyinScope): Promise<ClickToScopeReport> {
  const report: ClickToScopeReport = {
    page_url: location.href,
    profile_link_found: false,
    sub_tab_found: false,
  };
  // Step 1: get to /user/self if we're still on the homepage.
  // Click is preferred (mirrors user behaviour, avoids risk-control
  // friction); pushState fallback if no profile link found.
  const onProfile = location.pathname.startsWith("/user/");
  if (!onProfile) {
    const profileLink = findProfileLink();
    report.profile_link_found = profileLink !== null;
    if (profileLink) {
      profileLink.click();
    } else {
      window.history.pushState({}, "", "/user/self");
      window.dispatchEvent(new PopStateEvent("popstate"));
    }
    await sleep(2_500);
    report.page_url = location.href;
  }

  // Step 2: navigate to the target scope tab. Strategy: TRY a real
  // click first (it's what user would do — fires React's onClick,
  // which sets internal tab state AND attaches the
  // IntersectionObserver that drives lazy-loading on scroll). Fall
  // back to pushState only if click didn't change the URL within a
  // settle window.
  //
  // Why this matters: pushState alone routes the page to the right
  // tab visually but doesn't always wire up the lazy-load observer
  // (verified 2026-05-08 e2e — pages stayed at 12 cards after 5
  // stagnant scroll rounds). Real click on the tab element is what
  // makes "scroll → load more" work.
  const queryMap: Record<DouyinScope, string> = {
    dy_post: "",
    dy_collect: "?showTab=favorite_collection",
    dy_like: "?showTab=like",
    dy_follow: "?showTab=following",
  };
  const targetUrl = "/user/self" + queryMap[scope];
  const wantedSearch = queryMap[scope];

  const clickedTab = clickScopeSubTab(scope);
  report.sub_tab_found = clickedTab;
  if (clickedTab) {
    await sleep(1_500);
  }
  // After click, check whether URL actually changed to the right
  // showTab. If not (or click missed), pushState as fallback.
  const onTargetTab =
    wantedSearch === ""
      ? !location.search.includes("showTab=")
      : location.search.includes(wantedSearch.replace("?", ""));
  if (!onTargetTab) {
    const currentRelative = location.pathname + location.search;
    if (currentRelative === targetUrl) {
      window.history.pushState({}, "", "/user/self?_obc=" + Date.now());
      window.dispatchEvent(new PopStateEvent("popstate"));
      await sleep(400);
    }
    window.history.pushState({}, "", targetUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
    await sleep(2_000);
  }
  report.page_url = location.href;
  return report;
}

/**
 * Click the sub-tab element for the given scope. Returns true when a
 * candidate was found and clicked (independent of whether React
 * actually responded — caller checks URL afterwards). Uses both
 * data-e2e attribute selectors (most stable) and visible-text label
 * matching as fallbacks. For dy_post we click the "作品" tab to
 * ensure the post list's IntersectionObserver gets bound; pushState
 * to /user/self alone wasn't reliable.
 *
 * To improve React's onClick firing reliability we dispatch a real
 * MouseEvent (bubbles+composed+cancelable) instead of the simpler
 * `.click()` — some Douyin tab targets are wrapper spans whose
 * synthesized React handler depends on the bubbling phase.
 */
function clickScopeSubTab(scope: DouyinScope): boolean {
  const dataSelectors: Record<DouyinScope, string[]> = {
    dy_post: [
      '[data-e2e="user-tab-self"]',
      '[data-e2e="user-tab-post"]',
      '[data-e2e="user-tab-work"]',
    ],
    dy_collect: [
      '[data-e2e="user-favorite-tab"]',
      '[data-e2e="user-tab-favorite_collection"]',
      '[data-e2e="user-tab-favorite"]',
      'a[href*="favorite_collection"]',
    ],
    dy_like: [
      '[data-e2e="user-like-tab"]',
      '[data-e2e="user-tab-like"]',
      'a[href*="showTab=like"]',
    ],
    dy_follow: [
      '[data-e2e="user-following-tab"]',
      '[data-e2e="user-tab-following"]',
      'a[href*="showTab=following"]',
    ],
  };
  for (const sel of dataSelectors[scope]) {
    const el = document.querySelector<HTMLElement>(sel);
    if (el) {
      fireRealClick(el);
      return true;
    }
  }
  const labelMap: Record<DouyinScope, string> = {
    dy_post: "作品",
    dy_collect: "收藏",
    dy_like: "喜欢",
    dy_follow: "关注",
  };
  const label = labelMap[scope];
  const candidates = Array.from(
    document.querySelectorAll<HTMLElement>('a, button, [role="tab"], [class*="tab"]'),
  );
  for (const el of candidates) {
    if (el.textContent?.trim() === label) {
      fireRealClick(el);
      return true;
    }
  }
  return false;
}

function fireRealClick(el: HTMLElement): void {
  el.dispatchEvent(
    new MouseEvent("click", { bubbles: true, cancelable: true, composed: true }),
  );
}

/**
 * Scroll the scope's list to its last rendered card. Works for both
 * document-level scrollers and inner overflow:auto containers, since
 * Element.scrollIntoView walks up the ancestor chain and scrolls
 * whichever ancestor is the actual scroller. block:"end" puts the
 * card at the bottom of the viewport, ensuring the trailing sentinel
 * (the IntersectionObserver target Douyin uses to load more) becomes
 * visible.
 *
 * Returns true when a card was found and scrolled (so the caller can
 * decide between this strategy and the window.scrollBy fallback,
 * though we currently run both for max coverage).
 */
function scrollScopeListToEnd(scope: DouyinScope): boolean {
  const selector =
    scope === "dy_follow"
      ? 'a[href*="/user/MS4w"]'
      : 'a[href*="/video/"]';
  const anchors = document.querySelectorAll<HTMLElement>(selector);
  if (anchors.length === 0) return false;
  const last = anchors[anchors.length - 1];
  if (!last) return false;
  try {
    last.scrollIntoView({ block: "end", inline: "nearest", behavior: "auto" });
  } catch {
    // older browsers may not accept the options object — fall through
    return false;
  }
  return true;
}

/**
 * Detect Douyin's "no more content" indicator on the current tab.
 * Returns the matched phrase when found (so the caller can log it),
 * or "" when the list still has more to load.
 *
 * Strategy: walk every text-bearing element under document.body and
 * check the trimmed visible text. Limit to short text nodes
 * (< 30 chars) so we don't false-match a long description that
 * happens to contain "没有".
 */
const END_OF_FEED_PHRASES: readonly string[] = [
  "暂时没有更多",
  "没有更多了",
  "没有更多内容",
  "已加载全部",
  "已经到底",
  "到底啦",
  "已经到底啦",
  "no more",
  "the end",
];

/**
 * Tight visibility check — Douyin renders the "暂时没有更多了" sentinel
 * up-front and toggles its visibility, so plain textContent matching
 * triggers even when the list is far from exhausted. Require all of:
 *   - offsetParent != null (not display:none under any ancestor)
 *   - getComputedStyle visibility != hidden, opacity > 0
 *   - layout box has area
 *   - rect is at or below the upper half of the viewport (bottom
 *     sentinels live near the visible list bottom; hidden duplicates
 *     are usually at top:0 / negative offsets)
 */
function isTextNodeRenderedVisible(el: HTMLElement): boolean {
  if (!el.offsetParent && el !== document.body) return false;
  if (el.offsetWidth === 0 || el.offsetHeight === 0) return false;
  const style = window.getComputedStyle(el);
  if (style.visibility === "hidden" || style.display === "none") return false;
  if (parseFloat(style.opacity) === 0) return false;
  const rect = el.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return false;
  // Filter out top-of-viewport phantoms — real end-of-feed sentinels
  // sit at the bottom of the rendered list.
  if (rect.bottom < window.innerHeight * 0.4) return false;
  return true;
}

function detectEndOfFeed(): string {
  const candidates = Array.from(
    document.querySelectorAll<HTMLElement>(
      'div, span, p, [class*="loading"], [class*="end"], [class*="finish"]',
    ),
  );
  for (const el of candidates) {
    const text = (el.textContent ?? "").trim();
    if (!text || text.length > 30) continue;
    let matched = "";
    for (const phrase of END_OF_FEED_PHRASES) {
      if (text.includes(phrase)) {
        matched = phrase;
        break;
      }
    }
    if (!matched) continue;
    if (!isTextNodeRenderedVisible(el)) continue;
    return text;
  }
  return "";
}

export function classifyDouyinScopeCompletion(input: {
  itemCount: number;
  secUid: string;
  apiError: string;
  identityError?: string;
}): { status: ScopeResultPayload["status"]; error?: string } {
  if (!input.secUid) {
    return {
      status: "degraded",
      error: input.identityError || "no_sec_uid",
    };
  }
  if (input.apiError) {
    return { status: "degraded", error: input.apiError };
  }
  return { status: input.itemCount > 0 ? "ok" : "empty" };
}

async function runScope(msg: ScopeExecuteMessage): Promise<ScopeResultPayload> {
  const { BootstrapItemSink, dyShouldContinueScroll, ingestMainWorldFetchMessage } =
    await loadTaskExecutorHelpers();
  const { extractDouyinItemsFromDocument } = await loadDomExtractor();
  const sink = new BootstrapItemSink({ maxItemsPerScope: msg.max_items_per_scope });
  const allItems: DouyinBootstrapItem[] = [];
  // Per-scope counter: how many OPENBILICLAW_DOUYIN_AWEME_PAGE messages
  // the MAIN-world fetch-tap pushed into this scope's listener window.
  // Distinguished from items count: a message can carry items the sink
  // dedups away or items for the wrong scope, so a non-zero
  // aweme_messages_received with zero items is its own signature.
  let awemeMessagesReceived = 0;
  // DOM-extractor counter — separate from XHR/fetch tap. The DOM path
  // is the primary source for 喜欢/收藏/作品 because Douyin's React
  // Router often re-renders without firing a fresh /aweme/ XHR.
  let domItemsHarvested = 0;
  // API-harvest counters — primary source post-2026-05-08 since UI
  // scrolling failed to trigger Douyin's lazy-load (verified via
  // scroll_round telemetry — DOM stuck at 12-13 cards).
  let apiItemsHarvested = 0;
  let apiPagesFetched = 0;
  let apiError = "";
  let identityError = "";

  const onMessage = (event: MessageEvent): void => {
    if (!isSameWindowSameOriginDouyinMessage(event, window)) return;
    const data = event?.data as { type?: unknown } | null;
    if (data && typeof data === "object" && data.type === "OPENBILICLAW_DOUYIN_AWEME_PAGE") {
      awemeMessagesReceived += 1;
    }
    const newOnes = ingestMainWorldFetchMessage(event, sink);
    for (const item of newOnes) {
      if (item.scope === msg.scope) allItems.push(item);
    }
  };
  window.addEventListener("message", onMessage);

  // Snapshot the DOM at the current state and merge into the sink.
  // The sink dedups by scope:id, so calling this multiple times during
  // scroll is safe and cumulative.
  const harvestDomSnapshot = (): void => {
    const dom = extractDouyinItemsFromDocument(
      document,
      msg.scope,
      location.origin,
      msg.max_items_per_scope,
    );
    if (dom.length === 0) return;
    const newOnes = sink.ingest(dom);
    for (const item of newOnes) {
      if (item.scope === msg.scope) allItems.push(item);
    }
    domItemsHarvested += newOnes.length;
  };

  let clickReport: ClickToScopeReport = {
    page_url: location.href,
    profile_link_found: false,
    sub_tab_found: false,
  };
  let endOfFeedPhrase = "";
  try {
    // Navigate via UI clicks (more natural to Douyin risk control
    // than chrome.tabs.update URL jumps). clickToScope handles both
    // the homepage→profile transition and the sub-tab switch.
    clickReport = await clickToScope(msg.scope);

    // Re-inject MAIN-world fetch-tap after the click-driven SPA route.
    // Douyin's React app sometimes re-sets window.fetch on URL change,
    // which would silently bypass our wrap. Reinjecting guarantees
    // the latest live fetch is wrapped.
    reinjectFetchTap();

    // The MAIN-world fetch-tap auto-installs after waitForDouyinSdk
    // resolves. Give it a beat to settle so any pageload-time
    // /aweme/.../<scope>/ that fires AFTER our install gets captured.
    await sleep(POST_INSTALL_SETTLE_MS);

    // Initial DOM harvest before scrolling — captures whatever
    // Douyin's React Router rendered on landing. Also kicks the
    // page-bundle to fire its first /aweme/.../<scope>/ XHR which
    // gives our XHR tap a sec_uid to broadcast.
    harvestDomSnapshot();

    // API-driven harvest — primary path. RENDER_DATA and passive
    // sec_user_id values are diagnostic candidates only; the final identity
    // must come from the authoritative profile/self MAIN-world fetch (or its
    // same-tab confirmed cache), preserving live cookie/signing context.
    const identity = await resolveDouyinBootstrapSecUid();
    identityError = identity.error ?? "";
    if (identity.secUid) {
      const apiResult = await harvestScopeViaApiBridge(
        msg.scope,
        identity.secUid,
        msg.max_items_per_scope,
      );
      apiPagesFetched = apiResult.pages;
      apiError = apiResult.error ?? "";
      if (apiResult.items.length > 0) {
        const newOnes = sink.ingest(apiResult.items);
        apiItemsHarvested += newOnes.length;
        for (const item of newOnes) {
          if (item.scope === msg.scope) allItems.push(item);
        }
      }
    }

    let stagnantRounds = 0;
    for (let round = 0; round < msg.max_scroll_rounds; round += 1) {
      const beforeCount = sink.scopeCounts()[msg.scope];

      // Trigger Douyin's virtual-list pagination. Two strategies in
      // sequence:
      // 1. scrollIntoView the LAST scope-anchor card with block:"end".
      //    This works for arbitrary internal scroll containers
      //    (Douyin's user-tab list lives in [role="tabpanel"] or
      //    similar with overflow:auto, NOT document scroll), and is
      //    what triggers the IntersectionObserver-driven lazy load.
      // 2. window.scrollBy as fallback for cases where the document
      //    IS the scroller (e.g. some compact layouts, or follow tab).
      scrollScopeListToEnd(msg.scope);
      window.scrollBy({ top: window.innerHeight * 2, behavior: "auto" });
      await sleep(SCROLL_DELAY_MS);

      // Harvest from DOM after each scroll — newly virtualized cards
      // are now in the DOM whether or not Douyin re-fired an XHR.
      harvestDomSnapshot();

      const afterCount = sink.scopeCounts()[msg.scope];
      endOfFeedPhrase = detectEndOfFeed();
      stagnantRounds = afterCount > beforeCount ? 0 : stagnantRounds + 1;

      if (endOfFeedPhrase) break; // page tells us we're done
      if (
        !dyShouldContinueScroll({
          currentCount: afterCount,
          maxItemsPerScope: msg.max_items_per_scope,
          round: round + 1,
          maxScrollRounds: msg.max_scroll_rounds,
          stagnantRounds,
          maxStagnantScrollRounds: msg.max_stagnant_scroll_rounds,
        })
      ) {
        break;
      }
    }

    // Final DOM harvest pass after the scroll loop ends — picks up
    // anything Douyin rendered in the very last scroll batch that
    // we'd otherwise miss because the loop broke before re-scanning.
    harvestDomSnapshot();

    const completion = classifyDouyinScopeCompletion({
      itemCount: allItems.length,
      secUid: _detectedSecUid,
      apiError,
      identityError,
    });
    return {
      task_id: msg.task_id,
      scope: msg.scope,
      items: allItems,
      scope_count: sink.scopeCounts()[msg.scope],
      status: completion.status,
      error: completion.error,
      debug: {
        fetch_tap_install_status: _lastFetchTapInstallStatus,
        aweme_messages_received: awemeMessagesReceived,
        install_messages_received: _installMessagesReceived,
        dom_items_harvested: domItemsHarvested,
        api_items_harvested: apiItemsHarvested,
        api_pages_fetched: apiPagesFetched,
        api_error: apiError,
        sec_uid: _detectedSecUid,
        sec_uid_source: _detectedSecUidSource,
        identity_error: identityError,
        end_of_feed: endOfFeedPhrase,
        inject_status: msg.debug_inject_status,
        page_url: clickReport.page_url,
        profile_link_found: clickReport.profile_link_found,
        sub_tab_found: clickReport.sub_tab_found,
      },
    };
  } catch (err) {
    return {
      task_id: msg.task_id,
      scope: msg.scope,
      items: allItems,
      scope_count: sink.scopeCounts()[msg.scope],
      status: "failed",
      error: String(err),
      debug: {
        fetch_tap_install_status: _lastFetchTapInstallStatus,
        aweme_messages_received: awemeMessagesReceived,
        install_messages_received: _installMessagesReceived,
        dom_items_harvested: domItemsHarvested,
        api_items_harvested: apiItemsHarvested,
        api_pages_fetched: apiPagesFetched,
        api_error: apiError,
        sec_uid: _detectedSecUid,
        sec_uid_source: _detectedSecUidSource,
        identity_error: identityError,
        end_of_feed: endOfFeedPhrase,
        inject_status: msg.debug_inject_status,
        page_url: clickReport.page_url,
        profile_link_found: clickReport.profile_link_found,
        sub_tab_found: clickReport.sub_tab_found,
      },
    };
  } finally {
    window.removeEventListener("message", onMessage);
  }
}

// Adaptive search-scroll loop budget: up to 10 rounds, stop after 2
// consecutive rounds without item growth. Each round polls for growth
// every 250ms up to 3s instead of always burning a fixed sleep.
const SEARCH_SCROLL_ROUND_CAP = 10;
const SEARCH_SCROLL_STAGNANT_LIMIT = 2;
const SEARCH_SCROLL_GROWTH_POLL_INTERVAL_MS = 250;
const SEARCH_SCROLL_GROWTH_POLL_TIMEOUT_MS = 3_000;

async function runSearch(msg: SearchExecuteMessage): Promise<SearchResultPayload> {
  const { extractDouyinSearchItemsFromDocument, pickSearchScrollTarget } =
    await loadDomExtractor();
  const maxItems = Math.max(1, Math.floor(msg.max_items));
  let apiPagesFetched = 0;
  let apiItemsHarvested = 0;
  let domItemsHarvested = 0;
  let scrollRounds = 0;
  let apiError = "";
  let uiTriggered = false;
  let searchNavigationOk = false;
  let searchSubmitMethod = "none";
  const allItems: DouyinSearchItem[] = [];
  const passiveCollector = attachPassiveDiscoveryCollector(
    allItems,
    "dy_search",
    msg.resume_after_navigation === true,
  );

  try {
    reinjectFetchTap();
    await sleep(POST_INSTALL_SETTLE_MS);
    if (msg.resume_after_navigation) {
      // A real button/Enter submission may perform a full document load. The
      // original isolated-world promise disappears in that case, so the
      // dispatcher re-sends the task into the new document and asks us to
      // resume at collection. Never submit the search a second time here.
      uiTriggered = true;
      searchNavigationOk = isDouyinSearchResultUrl(location.href, msg.keyword);
      searchSubmitMethod = "navigation_resume";
    } else {
      const triggerResult = await triggerSearchUi(msg.keyword);
      uiTriggered = triggerResult.submitted;
      searchNavigationOk = triggerResult.navigated;
      searchSubmitMethod = triggerResult.method;
    }
    await sleep(2_000);

    // Passive-first pagination: scroll the REAL results container so the
    // page itself issues properly-signed page-2..N search requests, and
    // harvest them via the passive fetch-tap. Raw allItems re-accumulates
    // the same DOM cards every round, so growth is measured on the
    // deduped in-scope count.
    const dedupedCount = (): number =>
      filterDiscoveryItemsForScope(allItems, "dy_search", maxItems).length;
    const roundController = createScrollRoundController({
      roundCap: SEARCH_SCROLL_ROUND_CAP,
      stagnantLimit: SEARCH_SCROLL_STAGNANT_LIMIT,
      maxItems,
    });
    while (roundController.shouldContinue(dedupedCount())) {
      const domItems = extractDouyinSearchItemsFromDocument(
        document,
        location.origin,
        maxItems,
      );
      domItemsHarvested = Math.max(domItemsHarvested, domItems.length);
      allItems.push(...domItems);
      const countAtScroll = dedupedCount();
      // Re-pick the scroll target each round — the SPA can re-render the
      // results container. Fall back to window scrolling when no inner
      // scrollable container is found.
      const scrollTarget = pickSearchScrollTarget(document);
      if (scrollTarget) {
        scrollTarget.scrollTop = scrollTarget.scrollHeight;
      } else {
        window.scrollBy({ top: window.innerHeight * 2, behavior: "auto" });
      }
      for (
        let waited = 0;
        waited < SEARCH_SCROLL_GROWTH_POLL_TIMEOUT_MS;
        waited += SEARCH_SCROLL_GROWTH_POLL_INTERVAL_MS
      ) {
        await sleep(SEARCH_SCROLL_GROWTH_POLL_INTERVAL_MS);
        if (dedupedCount() > countAtScroll) break;
      }
      scrollRounds = roundController.roundsExecuted();
    }

    let items = filterDiscoveryItemsForScope(allItems, "dy_search", maxItems);
    if (items.length < maxItems) {
      try {
        const apiResult = await harvestSearchViaApiBridge(msg.keyword, maxItems);
        apiPagesFetched = apiResult.pages;
        apiItemsHarvested = apiResult.items.length;
        if (apiResult.error) apiError = apiResult.error;
        allItems.push(...apiResult.items);
        items = filterDiscoveryItemsForScope(allItems, "dy_search", maxItems);
      } catch (err) {
        apiError = String(err);
      }
    }
    const completion = classifyDouyinDiscoveryCompletion({
      source: "search",
      itemCount: items.length,
      injectStatus: msg.debug_inject_status,
      fetchTapInstallStatus: _lastFetchTapInstallStatus,
      apiError,
      uiTriggered,
      searchNavigationOk,
      alternateCollectionCompleted: apiPagesFetched > 0 && !apiError,
    });
    return {
      task_id: msg.task_id,
      keyword: msg.keyword,
      items,
      scope_count: items.length,
      status: completion.status,
      ...(completion.error ? { error: completion.error } : {}),
      debug: {
        fetch_tap_install_status: _lastFetchTapInstallStatus,
        api_pages_fetched: apiPagesFetched,
        api_items_harvested: apiItemsHarvested,
        dom_items_harvested: domItemsHarvested,
        api_error: apiError,
        ui_triggered: uiTriggered,
        search_navigation_ok: searchNavigationOk,
        search_submit_method: searchSubmitMethod,
        navigation_resumed: msg.resume_after_navigation === true,
        passive_items_harvested: passiveCollector.passiveCount(),
        passive_responses_observed: passiveCollector.responseCount(),
        early_buffer_items: passiveCollector.earlyBufferCount(),
        scroll_rounds: scrollRounds,
        inject_status: msg.debug_inject_status,
        page_url: location.href,
      },
    };
  } catch (err) {
    const items = filterDiscoveryItemsForScope(allItems, "dy_search", maxItems);
    return {
      task_id: msg.task_id,
      keyword: msg.keyword,
      items,
      scope_count: items.length,
      status: items.length > 0 ? "ok" : "failed",
      error: items.length > 0 ? undefined : String(err),
      debug: {
        fetch_tap_install_status: _lastFetchTapInstallStatus,
        api_pages_fetched: apiPagesFetched,
        api_items_harvested: apiItemsHarvested,
        dom_items_harvested: domItemsHarvested,
        api_error: apiError || String(err),
        ui_triggered: uiTriggered,
        search_navigation_ok: searchNavigationOk,
        search_submit_method: searchSubmitMethod,
        navigation_resumed: msg.resume_after_navigation === true,
        passive_items_harvested: passiveCollector.passiveCount(),
        passive_responses_observed: passiveCollector.responseCount(),
        early_buffer_items: passiveCollector.earlyBufferCount(),
        scroll_rounds: scrollRounds,
        inject_status: msg.debug_inject_status,
        page_url: location.href,
      },
    };
  } finally {
    passiveCollector.detach();
  }
}

async function runHot(msg: HotExecuteMessage): Promise<HotResultPayload> {
  const { extractDouyinSearchItemsFromDocument } = await loadDomExtractor();
  const maxItems = Math.max(1, Math.floor(msg.max_items));
  let apiPagesFetched = 0;
  let apiItemsHarvested = 0;
  let domItemsHarvested = 0;
  let apiError = "";
  let seedAwemeId = "";
  let uiTriggered = false;
  const fallbackSeedAwemeId = String(msg.seed_aweme_id ?? "").trim();
  const allItems: DouyinSearchItem[] = [];
  const passiveCollector = attachPassiveDiscoveryCollector(allItems, "dy_hot");

  try {
    reinjectFetchTap();
    await sleep(POST_INSTALL_SETTLE_MS);
    uiTriggered = await triggerHotUi(msg.sentence_id, msg.word);
    await sleep(2_000);
    seedAwemeId = await waitForCurrentVideoAwemeId(2_000);
    if (!seedAwemeId && fallbackSeedAwemeId) {
      seedAwemeId = fallbackSeedAwemeId;
    }

    for (let round = 0; round < 4 && allItems.length < maxItems; round += 1) {
      const domItems = extractDouyinSearchItemsFromDocument(
        document,
        location.origin,
        maxItems,
      ).map((item) => ({
        ...item,
        scope: "dy_hot" as const,
        sentence_id: msg.sentence_id,
        hot_word: msg.word,
        seed_aweme_id: item.seed_aweme_id || seedAwemeId,
      }));
      domItemsHarvested = Math.max(domItemsHarvested, domItems.length);
      allItems.push(...domItems);
      window.scrollBy({ top: window.innerHeight * 2, behavior: "auto" });
      await sleep(1_000);
    }

    let items = filterDiscoveryItemsForScope(allItems, "dy_hot", maxItems);
    if (items.length < maxItems && seedAwemeId) {
      try {
        const apiResult = await harvestHotRelatedViaApiBridge(
          seedAwemeId,
          maxItems,
          msg.sentence_id,
          msg.word,
        );
        const apiItems = apiResult.items.map((item) => ({
          ...item,
          hot_word: item.hot_word || msg.word,
          sentence_id: item.sentence_id || msg.sentence_id,
          seed_aweme_id: item.seed_aweme_id || seedAwemeId,
        }));
        apiPagesFetched = apiResult.pages;
        apiItemsHarvested = apiItems.length;
        if (apiResult.error) apiError = apiResult.error;
        allItems.push(...apiItems);
        items = filterDiscoveryItemsForScope(allItems, "dy_hot", maxItems);
      } catch (err) {
        apiError = String(err);
      }
    }
    const completion = classifyDouyinDiscoveryCompletion({
      source: "hot",
      itemCount: items.length,
      injectStatus: msg.debug_inject_status,
      fetchTapInstallStatus: _lastFetchTapInstallStatus,
      apiError,
      uiTriggered,
      alternateCollectionCompleted: apiPagesFetched > 0 && !apiError,
    });
    return {
      task_id: msg.task_id,
      sentence_id: msg.sentence_id,
      word: msg.word,
      items,
      scope_count: items.length,
      status: completion.status,
      ...(completion.error ? { error: completion.error } : {}),
      debug: {
        fetch_tap_install_status: _lastFetchTapInstallStatus,
        api_pages_fetched: apiPagesFetched,
        api_items_harvested: apiItemsHarvested,
        api_error: apiError,
        dom_items_harvested: domItemsHarvested,
        passive_items_harvested: passiveCollector.passiveCount(),
        passive_responses_observed: passiveCollector.responseCount(),
        early_buffer_items: passiveCollector.earlyBufferCount(),
        seed_aweme_id: seedAwemeId,
        ui_triggered: uiTriggered,
        inject_status: msg.debug_inject_status,
        page_url: location.href,
      },
    };
  } catch (err) {
    const items = filterDiscoveryItemsForScope(allItems, "dy_hot", maxItems);
    return {
      task_id: msg.task_id,
      sentence_id: msg.sentence_id,
      word: msg.word,
      items,
      scope_count: items.length,
      status: items.length > 0 ? "ok" : "failed",
      error: items.length > 0 ? undefined : String(err),
      debug: {
        fetch_tap_install_status: _lastFetchTapInstallStatus,
        api_pages_fetched: apiPagesFetched,
        api_items_harvested: apiItemsHarvested,
        api_error: apiError || String(err),
        dom_items_harvested: domItemsHarvested,
        passive_items_harvested: passiveCollector.passiveCount(),
        passive_responses_observed: passiveCollector.responseCount(),
        early_buffer_items: passiveCollector.earlyBufferCount(),
        seed_aweme_id: seedAwemeId,
        ui_triggered: uiTriggered,
        inject_status: msg.debug_inject_status,
        page_url: location.href,
      },
    };
  } finally {
    passiveCollector.detach();
  }
}

async function runFeed(msg: FeedExecuteMessage): Promise<FeedResultPayload> {
  const { extractDouyinSearchItemsFromDocument } = await loadDomExtractor();
  const maxItems = Math.max(1, Math.floor(msg.max_items));
  let apiPagesFetched = 0;
  let apiItemsHarvested = 0;
  let domItemsHarvested = 0;
  let apiError = "";
  const allItems: DouyinSearchItem[] = [];
  const passiveCollector = attachPassiveDiscoveryCollector(allItems, "dy_feed");

  try {
    reinjectFetchTap();
    await sleep(POST_INSTALL_SETTLE_MS);

    for (let round = 0; round < 4 && allItems.length < maxItems; round += 1) {
      const domItems = extractDouyinSearchItemsFromDocument(
        document,
        location.origin,
        maxItems,
        true,
      ).map((item) => ({ ...item, scope: "dy_feed" as const }));
      domItemsHarvested = Math.max(domItemsHarvested, domItems.length);
      allItems.push(...domItems);
      window.scrollBy({ top: window.innerHeight * 2, behavior: "auto" });
      await sleep(1_000);
    }

    const items = filterDiscoveryItemsForScope(allItems, "dy_feed", maxItems);
    const completion = classifyDouyinDiscoveryCompletion({
      source: "feed",
      itemCount: items.length,
      injectStatus: msg.debug_inject_status,
      fetchTapInstallStatus: _lastFetchTapInstallStatus,
      apiError,
      passiveResponsesObserved: passiveCollector.responseCount(),
      domItemsHarvested,
    });
    return {
      task_id: msg.task_id,
      items,
      scope_count: items.length,
      status: completion.status,
      ...(completion.error ? { error: completion.error } : {}),
      debug: {
        fetch_tap_install_status: _lastFetchTapInstallStatus,
        api_pages_fetched: apiPagesFetched,
        api_items_harvested: apiItemsHarvested,
        dom_items_harvested: domItemsHarvested,
        passive_items_harvested: passiveCollector.passiveCount(),
        passive_responses_observed: passiveCollector.responseCount(),
        early_buffer_items: passiveCollector.earlyBufferCount(),
        api_error: apiError,
        inject_status: msg.debug_inject_status,
        page_url: location.href,
      },
    };
  } catch (err) {
    const items = filterDiscoveryItemsForScope(allItems, "dy_feed", maxItems);
    return {
      task_id: msg.task_id,
      items,
      scope_count: items.length,
      status: items.length > 0 ? "ok" : "failed",
      error: items.length > 0 ? undefined : String(err),
      debug: {
        fetch_tap_install_status: _lastFetchTapInstallStatus,
        api_pages_fetched: apiPagesFetched,
        api_items_harvested: apiItemsHarvested,
        dom_items_harvested: domItemsHarvested,
        passive_items_harvested: passiveCollector.passiveCount(),
        passive_responses_observed: passiveCollector.responseCount(),
        early_buffer_items: passiveCollector.earlyBufferCount(),
        api_error: apiError || String(err),
        inject_status: msg.debug_inject_status,
        page_url: location.href,
      },
    };
  } finally {
    passiveCollector.detach();
  }
}

export function isValidScopeExecuteMessage(value: unknown): value is ScopeExecuteMessage {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (typeof v.task_id !== "string" || !v.task_id) return false;
  const KNOWN: readonly DouyinScope[] = ["dy_post", "dy_collect", "dy_like", "dy_follow"];
  if (!KNOWN.includes(v.scope as DouyinScope)) return false;
  if (typeof v.max_items_per_scope !== "number") return false;
  if (typeof v.max_scroll_rounds !== "number") return false;
  if (typeof v.max_stagnant_scroll_rounds !== "number") return false;
  return true;
}

export function isValidSearchExecuteMessage(value: unknown): value is SearchExecuteMessage {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (typeof v.task_id !== "string" || !v.task_id) return false;
  if (typeof v.keyword !== "string" || !v.keyword.trim()) return false;
  if (typeof v.max_items !== "number") return false;
  if (
    v.resume_after_navigation !== undefined &&
    typeof v.resume_after_navigation !== "boolean"
  ) {
    return false;
  }
  return true;
}

export function isValidHotExecuteMessage(value: unknown): value is HotExecuteMessage {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (typeof v.task_id !== "string" || !v.task_id) return false;
  if (typeof v.sentence_id !== "string" || !v.sentence_id.trim()) return false;
  if (typeof v.max_items !== "number") return false;
  return true;
}

export function isValidFeedExecuteMessage(value: unknown): value is FeedExecuteMessage {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (typeof v.task_id !== "string" || !v.task_id) return false;
  if (typeof v.max_items !== "number") return false;
  return Number.isFinite(v.max_items) && v.max_items > 0;
}

// A SPA URL change keeps the original isolated world alive while the
// background navigation watcher may also send a resume message. Keep one
// execution per task/keyword in a document; a genuine full navigation gets a
// fresh JS world and therefore accepts the resume exactly once.
const activeSearchExecutions = new Set<string>();

export function registerDyScopeExecutor(): void {
  if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.onMessage) return;
  chrome.runtime.onMessage.addListener(
    (message: Record<string, unknown>, _sender, sendResponse) => {
      if (message.action !== "DY_SCOPE_EXECUTE") return false;
      const data = message.data;
      if (!isValidScopeExecuteMessage(data)) {
        return false;
      }

      void runScope(data).then((result) => {
        chrome.runtime.sendMessage({ action: "DY_SCOPE_RESULT", data: result }).catch(() => {
          // Service worker may have torn down between scopes; the dispatcher
          // will eventually time out and report the task failure.
        });
      });

      // We don't use sendResponse — return false so the channel closes.
      return false;
    },
  );
  chrome.runtime.onMessage.addListener(
    (message: Record<string, unknown>, _sender, sendResponse) => {
      if (message.action !== "DY_SEARCH_EXECUTE") return false;
      const data = message.data;
      if (!isValidSearchExecuteMessage(data)) {
        return false;
      }

      const executionKey = `${data.task_id}\u0000${data.keyword}`;
      if (activeSearchExecutions.has(executionKey)) return false;
      activeSearchExecutions.add(executionKey);
      void runSearch(data)
        .then((result) => {
          return chrome.runtime
            .sendMessage({ action: "DY_SEARCH_RESULT", data: result })
            .catch(() => {
              // The dispatcher timeout is the retry/failure path if the worker
              // disappears before the result can be delivered.
            });
        })
        .finally(() => {
          activeSearchExecutions.delete(executionKey);
        });

      return false;
    },
  );
  chrome.runtime.onMessage.addListener(
    (message: Record<string, unknown>, _sender, sendResponse) => {
      if (message.action !== "DY_HOT_EXECUTE") return false;
      const data = message.data;
      if (!isValidHotExecuteMessage(data)) {
        return false;
      }

      void runHot(data).then((result) => {
        chrome.runtime.sendMessage({ action: "DY_HOT_RESULT", data: result }).catch(() => {
          // The dispatcher timeout is the retry/failure path if delivery
          // fails while the service worker is being recycled.
        });
      });

      return false;
    },
  );
  chrome.runtime.onMessage.addListener(
    (message: Record<string, unknown>, _sender, sendResponse) => {
      if (message.action !== "DY_FEED_EXECUTE") return false;
      const data = message.data;
      if (!isValidFeedExecuteMessage(data)) {
        return false;
      }

      void runFeed(data).then((result) => {
        chrome.runtime.sendMessage({ action: "DY_FEED_RESULT", data: result }).catch(() => {
          // The dispatcher timeout is the retry/failure path if delivery
          // fails while the service worker is being recycled.
        });
      });

      return false;
    },
  );
}

if (typeof chrome !== "undefined" && chrome.runtime) {
  registerDyScopeExecutor();
  // eslint-disable-next-line no-console
  console.debug("[OpenBiliClaw] dy content script registered (isolated world)");
}
