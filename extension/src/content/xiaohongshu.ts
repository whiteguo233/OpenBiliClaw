/**
 * OpenBiliClaw — Xiaohongshu content script entry.
 *
 * Injected into xiaohongshu.com pages. Wires the generic collector
 * kernel to the xhs-specific adapter. MVP scope: snapshot, click,
 * scroll, search — like/collect/comment are deliberately skipped.
 *
 * Also runs a strictly passive URL collector: when the user scrolls or
 * lands on an xhs page, we extract note URLs that are already visible and
 * forward them to the backend for enrichment. We never scroll ourselves.
 */

import { startCollector } from "./kernel.js";
import { xiaohongshuAdapter } from "../shared/platforms/xiaohongshu.js";
import { registerE2EExecutor } from "./e2e-executor.ts";
import {
  classifyXhsPageType,
  collectInViewportNoteUrls,
  dedupeObservedUrls,
  extractNoteMetadataFromAnchor,
  filterSelfAuthoredNotes,
  type AnchorLike,
  type ViewportRect,
  type XhsNoteMetadata,
  type XhsSelfInfo,
  type XhsUrlObservation,
} from "./xhs/passive.js";
import {
  extractBootstrapStateFromDocument,
  extractSelfInfoFromState,
} from "./xhs/bootstrap.js";
import { registerTaskExecutor } from "./xhs/task-executor.js";

startCollector(xiaohongshuAdapter);
registerTaskExecutor();
registerE2EExecutor("xiaohongshu");

// ── Token sniffer bridge (isolated world receiver) ──────────────────
//
// The MAIN-world script at `dist/main/xhs-token-sniffer.js` wraps xhs's
// own fetch/XHR and postMessages `(note_id, xsec_token)` pairs it finds
// in API responses. We buffer them here and POST to the backend so the
// `_backfill_xhs_tokens` path can upgrade cached bare URLs to
// tokenized ones. Without this, search-page-sourced notes stay bare
// forever and clicking them hits xhs's 300031 access-denied wall.
// Debounce is short (250 ms) because background task-executor tabs often
// close within ~2 s of load — a 1 s+ debounce loses every token to the
// tab closure. Passive scroll pages keep collecting across the debounce
// window just fine.
const TOKEN_FLUSH_DEBOUNCE_MS = 250;
const TOKEN_BATCH_MAX = 50;

interface TokenPair {
  note_id: string;
  xsec_token: string;
}

const tokenBuffer = new Map<string, string>();
let tokenFlushTimer: number | null = null;

function flushTokensNow(): void {
  if (tokenFlushTimer !== null) {
    window.clearTimeout(tokenFlushTimer);
    tokenFlushTimer = null;
  }
  if (tokenBuffer.size === 0) return;
  const pairs: TokenPair[] = [];
  for (const [note_id, xsec_token] of tokenBuffer) {
    pairs.push({ note_id, xsec_token });
    if (pairs.length >= TOKEN_BATCH_MAX) break;
  }
  for (const { note_id } of pairs) tokenBuffer.delete(note_id);
  chrome.runtime.sendMessage({ action: "XHS_TOKENS_OBSERVED", data: { pairs } });
}

function scheduleTokenFlush(): void {
  if (tokenFlushTimer !== null) window.clearTimeout(tokenFlushTimer);
  tokenFlushTimer = window.setTimeout(flushTokensNow, TOKEN_FLUSH_DEBOUNCE_MS);
}

window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const data = event.data as { source?: string; pairs?: TokenPair[] } | null;
  if (!data || data.source !== "obc-xhs-sniffer") return;
  if (!Array.isArray(data.pairs) || data.pairs.length === 0) return;
  for (const pair of data.pairs) {
    if (pair?.note_id && pair?.xsec_token) {
      tokenBuffer.set(pair.note_id, pair.xsec_token);
    }
  }
  scheduleTokenFlush();
});

// When the tab is about to die (navigation, close, or background
// task-executor tearing down the tab), flush any buffered tokens
// synchronously. Without this, task-executor tabs lose every token
// they collected because the debounced flush never fires in time.
window.addEventListener("pagehide", flushTokensNow);
window.addEventListener("beforeunload", flushTokensNow);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") flushTokensNow();
});

const PASSIVE_SCROLL_DEBOUNCE_MS = 500;
const PASSIVE_TOLERANCE_BELOW_PX = 400;
const PASSIVE_MAX_URLS_PER_BATCH = 20;
const PASSIVE_ANCHOR_SELECTOR = [
  'a[href*="/explore/"]',
  'a[href*="/discovery/item/"]',
].join(",");

const reportedUrls = new Set<string>();

function readViewport(): ViewportRect {
  const height = window.innerHeight || document.documentElement.clientHeight || 0;
  return { top: 0, bottom: height, height };
}

function snapshotAnchors(): AnchorLike[] {
  const nodes = document.querySelectorAll<HTMLAnchorElement>(PASSIVE_ANCHOR_SELECTOR);
  const anchors: AnchorLike[] = [];
  nodes.forEach((node) => {
    anchors.push({ href: node.href, rect: node.getBoundingClientRect() });
  });
  return anchors;
}

/**
 * When the user is on a note detail page, window.location itself carries
 * the authoritative xsec_token for that note — the most reliable source
 * of tokens we have (xhs search-result listings don't put tokens in
 * anchor hrefs). We synthesise an extra anchor from location.href so the
 * collector can preserve it just like any other observed note URL.
 */
function selfNoteAnchor(): AnchorLike | null {
  const { pathname, search } = window.location;
  if (!pathname.startsWith("/explore/") && !pathname.startsWith("/discovery/item/")) {
    return null;
  }
  const params = new URLSearchParams(search);
  if (!params.has("xsec_token")) return null;
  // Rect above the viewport would be skipped; put it inside so the
  // collector actually picks it up.
  const rect = new DOMRect(0, 0, 1, 1);
  return { href: window.location.href, rect };
}

function readPageSelfInfo(): XhsSelfInfo | null {
  // v0.3.10+: every logged-in XHS page exposes the user fingerprint via
  // ``__INITIAL_STATE__.user``. Reading it here (not just inside the
  // bootstrap_profile task) lets backend persist self_info on the very
  // first passive scrape — closing the race where search-task results
  // pollute the pool before bootstrap_profile ever runs.
  try {
    const state = extractBootstrapStateFromDocument(document);
    if (!state) return null;
    return extractSelfInfoFromState(state);
  } catch {
    return null;
  }
}

function runPassiveCollection(): void {
  const anchors = snapshotAnchors();
  const selfAnchor = selfNoteAnchor();
  if (selfAnchor !== null) {
    anchors.push(selfAnchor);
  }
  const visible = collectInViewportNoteUrls(anchors, readViewport(), {
    baseUrl: window.location.href,
    toleranceBelowPx: PASSIVE_TOLERANCE_BELOW_PX,
  });
  const fresh = dedupeObservedUrls(visible, reportedUrls);
  if (fresh.length === 0) return;

  const freshSet = new Set(fresh);
  const baseUrl = window.location.href;

  // Extract metadata from DOM for fresh URLs
  const notes: XhsNoteMetadata[] = [];
  const anchorEls = document.querySelectorAll<HTMLAnchorElement>(PASSIVE_ANCHOR_SELECTOR);
  anchorEls.forEach((el) => {
    const meta = extractNoteMetadataFromAnchor(el, baseUrl);
    if (meta && freshSet.has(meta.url) && notes.length < PASSIVE_MAX_URLS_PER_BATCH) {
      notes.push(meta);
      freshSet.delete(meta.url); // avoid duplicates from multiple anchors with same URL
    }
  });

  // v0.3.10+: scrape-time self-author drop. Backend filters again on
  // ingest, but doing it here avoids round-tripping notes that XHS's
  // search/explore feed echoes back to the logged-in author.
  const selfInfo = readPageSelfInfo();
  const filteredNotes = filterSelfAuthoredNotes(notes, selfInfo);

  const observation: XhsUrlObservation = {
    urls: fresh.slice(0, PASSIVE_MAX_URLS_PER_BATCH),
    notes: filteredNotes,
    page_type: classifyXhsPageType(baseUrl),
    observed_at: Date.now(),
    ...(selfInfo ? { self_info: selfInfo } : {}),
  };
  chrome.runtime.sendMessage({ action: "XHS_URLS_OBSERVED", data: observation });
}

let scrollTimer: number | null = null;
window.addEventListener(
  "scroll",
  () => {
    if (scrollTimer !== null) window.clearTimeout(scrollTimer);
    scrollTimer = window.setTimeout(runPassiveCollection, PASSIVE_SCROLL_DEBOUNCE_MS);
  },
  { passive: true },
);

// URL navigation in a SPA resets the "already reported" window so users
// don't miss a note just because they saw another one with the same id in
// a previous page-session.
window.addEventListener("popstate", () => {
  reportedUrls.clear();
  window.setTimeout(runPassiveCollection, PASSIVE_SCROLL_DEBOUNCE_MS);
});

window.setTimeout(runPassiveCollection, PASSIVE_SCROLL_DEBOUNCE_MS);

console.log(
  "[OpenBiliClaw] Xiaohongshu behavior collector initialized on",
  xiaohongshuAdapter.detectPageType(window.location.href),
  "page",
);
