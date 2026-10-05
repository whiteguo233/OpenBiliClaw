/**
 * MAIN-world script — observes xhs's own fetch/XHR responses to learn
 * ``(note_id, xsec_token)`` pairs and postMessages them to the isolated
 * content script. For the search endpoint it also emits the same minimal
 * public card metadata that the DOM collector reports, allowing hidden
 * discovery tabs to work even when XHS skips mounting its virtualized grid.
 *
 * Why MAIN world? Content scripts run in an isolated JS context, so
 * overriding ``window.fetch`` there doesn't intercept the page's own
 * fetches. MAIN-world scripts share state with the page, letting us
 * wrap the same ``fetch`` / ``XMLHttpRequest`` objects the xhs React
 * app uses.
 *
 * What we do NOT do: mutate requests, fingerprint the user, or expose raw
 * responses. Only token pairs and normalized public search-card fields leave
 * MAIN world.
 *
 * The isolated content script listens via ``window.addEventListener
 * ("message", ...)`` and filters by ``source: "obc-xhs-sniffer"``.
 */

import {
  extractXhsSearchResponseNotes,
  isXhsSearchApiUrl,
  type XhsSearchResponseNote,
} from "../shared/xhs-search-response.ts";
import {
  extractPublishedTimesFromPayload,
  type XhsPublishedTime,
} from "../shared/xhs-published-at.ts";

interface TokenPair {
  note_id: string;
  xsec_token: string;
}

const POST_MESSAGE_SOURCE = "obc-xhs-sniffer";
const REPLAY_REQUEST_SOURCE = "obc-xhs-search-replay-request";
let latestSearchNotes: XhsSearchResponseNote[] = [];

const NOTE_ID_KEYS = ["note_id", "noteId", "id"] as const;
const TOKEN_KEYS = ["xsec_token", "xsecToken"] as const;

/**
 * Walk a JSON blob and harvest every plausible ``(note_id, xsec_token)``
 * pair. xhs's internal responses vary wildly in shape (feed API, search
 * API, user-profile API all nest differently) so we do a depth-first
 * scan rather than targeting specific paths that break on schema drift.
 *
 * Exported so the test file can exercise it without a browser.
 */
export function extractTokenPairs(payload: unknown): TokenPair[] {
  const out: TokenPair[] = [];
  const seen = new Set<string>();

  function pushIfNew(pair: TokenPair): void {
    if (!pair.note_id || !pair.xsec_token) return;
    const key = `${pair.note_id}|${pair.xsec_token}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push(pair);
  }

  function walk(node: unknown): void {
    if (node === null || typeof node !== "object") return;

    if (Array.isArray(node)) {
      for (const child of node) walk(child);
      return;
    }

    const obj = node as Record<string, unknown>;
    let note_id = "";
    let xsec_token = "";
    for (const k of NOTE_ID_KEYS) {
      const v = obj[k];
      if (typeof v === "string" && /^[0-9a-f]{24}$/i.test(v)) {
        note_id = v;
        break;
      }
    }
    for (const k of TOKEN_KEYS) {
      const v = obj[k];
      if (typeof v === "string" && v.length > 0) {
        xsec_token = v;
        break;
      }
    }
    pushIfNew({ note_id, xsec_token });

    for (const value of Object.values(obj)) walk(value);
  }

  walk(payload);
  return out;
}

function emit(
  pairs: TokenPair[],
  searchNotes?: XhsSearchResponseNote[],
  publishedNotes?: XhsPublishedTime[],
): void {
  if (pairs.length === 0 && searchNotes === undefined && !publishedNotes?.length) return;
  window.postMessage(
    {
      source: POST_MESSAGE_SOURCE,
      pairs,
      search_notes: searchNotes,
      published_notes: publishedNotes,
    },
    "*",
  );
}

function isXhsApiUrl(url: string): boolean {
  // xhs serves its web APIs from edith.xiaohongshu.com and
  // /api/sns/web/ paths on the main origin. Match both.
  return url.includes("/api/sns/web/") || url.includes("edith.xiaohongshu.com");
}

async function parseResponseSafely(res: Response): Promise<unknown> {
  // Clone before reading — the page still needs the original body.
  try {
    const clone = res.clone();
    const text = await clone.text();
    if (!text) return null;
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function handleApiPayload(url: string, payload: unknown): void {
  const pairs = extractTokenPairs(payload);
  const publishedNotes = extractPublishedTimesFromPayload(payload);
  if (isXhsSearchApiUrl(url)) {
    latestSearchNotes = extractXhsSearchResponseNotes(payload);
    emit(pairs, latestSearchNotes, publishedNotes);
    return;
  }
  emit(pairs, undefined, publishedNotes);
}

type TaggedXhr = XMLHttpRequest & { __obcXhsUrl?: string };

/**
 * Wrap window.fetch and XMLHttpRequest so every xhs API response is
 * scanned for ``(note_id, xsec_token)`` pairs. Called once at module
 * load time in MAIN-world context; factored out so unit tests can
 * import ``extractTokenPairs`` without triggering browser-only code.
 */
function installSniffer(): void {
  const originalFetch = window.fetch.bind(window);
  window.fetch = async function wrappedFetch(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    const response = await originalFetch(input, init);
    try {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : input.url;
      if (url && isXhsApiUrl(url)) {
        void parseResponseSafely(response).then((json) => {
          if (json !== null) handleApiPayload(url, json);
        });
      }
    } catch {
      // swallow — never break the page's fetch
    }
    return response;
  };

  const XhrProto = XMLHttpRequest.prototype;
  const originalOpen = XhrProto.open;
  const originalSend = XhrProto.send;

  XhrProto.open = function patchedOpen(
    this: TaggedXhr,
    method: string,
    url: string | URL,
    async?: boolean,
    user?: string | null,
    password?: string | null,
  ): void {
    this.__obcXhsUrl = typeof url === "string" ? url : url.href;
    return originalOpen.call(
      this,
      method,
      url,
      async ?? true,
      user ?? null,
      password ?? null,
    );
  };

  XhrProto.send = function patchedSend(
    this: TaggedXhr,
    body?: Document | XMLHttpRequestBodyInit | null,
  ): void {
    const url = this.__obcXhsUrl ?? "";
    if (url && isXhsApiUrl(url)) {
      this.addEventListener("load", () => {
        try {
          if (this.responseType === "" || this.responseType === "text") {
            const text = this.responseText;
            if (text) {
              const json = JSON.parse(text);
              handleApiPayload(url, json);
            }
          } else if (this.responseType === "json") {
            handleApiPayload(url, this.response);
          }
        } catch {
          // swallow
        }
      });
    }
    return originalSend.call(this, body ?? null);
  };

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data as { source?: string } | null;
    if (data?.source !== REPLAY_REQUEST_SOURCE) return;
    emit([], latestSearchNotes);
  });

  console.debug("[OpenBiliClaw] xhs token sniffer installed (MAIN world)");
}

// Only auto-install when loaded in a browser context (MAIN world content
// script). Guard on ``typeof window`` so node test runners that import
// this module for ``extractTokenPairs`` don't crash at load time.
if (typeof window !== "undefined" && typeof XMLHttpRequest !== "undefined") {
  installSniffer();
}
