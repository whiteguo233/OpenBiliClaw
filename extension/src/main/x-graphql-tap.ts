/**
 * X (Twitter) MAIN-world GraphQL tap.
 *
 * Pattern (mirrors `dy-fetch-tap.ts` + `xhs-token-sniffer.ts`): wrap
 * `window.fetch` and `XMLHttpRequest` in MAIN world to **observe** the
 * user's own engagement mutations on x.com / twitter.com, then
 * `postMessage({ source: "obc-x-tap", ... })` back to the isolated-world
 * content script (`content/x.ts`), which forwards them as BEHAVIOR_EVENTs.
 *
 * Why MAIN world? Content scripts run in an isolated JS context, so
 * overriding `window.fetch` there doesn't intercept the page's own
 * fetches. A MAIN-world script shares state with the page and wraps the
 * same `fetch` / `XMLHttpRequest` the X React app uses.
 *
 * What we capture (engagement + opens only — discovery timelines are
 * server-side, NOT here):
 *   - FavoriteTweet   (GraphQL POST) → like
 *   - CreateBookmark  (GraphQL POST) → favorite
 *   - CreateRetweet   (GraphQL POST) → share
 *   - CreateTweet     (GraphQL POST, only when variables.reply present) → comment
 *   - TweetDetail     (GraphQL GET)  → view   (opened tweet)
 *   - friendships/create.json (REST POST, form-encoded) → follow
 *
 * CRITICAL constraints:
 *   1. Match the GraphQL **operation name** in
 *      `/i/api/graphql/<queryId>/<OperationName>`, treating the hashed
 *      queryId as a wildcard — X rotates it every ~2-4 weeks.
 *   2. The tap is **observation-only**: it forwards the page's (input,
 *      init) to the original fetch byte-identical, and only reads a
 *      `Response.clone()`. It never mutates the page's request or
 *      response.
 *
 * The module does NOT auto-install when imported under node:test (the
 * side-effect block is guarded by `typeof window`). `classifyXResponseUrl`
 * and `parseXMutation` are pure and unit-tested directly.
 */

const POST_MESSAGE_SOURCE = "obc-x-tap";

export type XEventType = "like" | "favorite" | "share" | "comment" | "view" | "follow";

/** A request as the tap observes it: URL + raw request/response bodies. */
export interface CapturedXRequest {
  url: string;
  requestBody: string;
  responseBody: string;
}

/** Parsed engagement signal posted to the content script. */
export interface XEngagement {
  type: XEventType;
  /** Present for everything except follow. */
  tweet_id?: string;
  /** Present for follow only (REST friendships/create carries user_id). */
  user_id?: string;
}

// GraphQL operation name → event type. Engagement + opens only; discovery
// timelines (HomeTimeline / SearchTimeline / UserTweets / …) are
// deliberately absent so we return null for them.
const GRAPHQL_OP_EVENTS: Record<string, XEventType> = {
  FavoriteTweet: "like",
  CreateBookmark: "favorite",
  CreateRetweet: "share",
  CreateTweet: "comment", // only counts as a comment when a reply target is present
  TweetDetail: "view",
};

/**
 * Extract the GraphQL operation name from a URL of the form
 * `/i/api/graphql/<queryId>/<OperationName>(?...)`. Returns "" if the URL
 * isn't a GraphQL endpoint. The queryId segment is ignored.
 */
function graphqlOperationName(url: string): string {
  const match = url.match(/\/i\/api\/graphql\/[^/]+\/([A-Za-z0-9_]+)/);
  return match?.[1] ?? "";
}

function isFollowRestUrl(url: string): boolean {
  // Strip query string before matching the path.
  const path = url.split("?", 1)[0] ?? "";
  return path.includes("/i/api/1.1/friendships/create.json");
}

/**
 * Map a request URL to the event type we'd record, or null if it's not an
 * endpoint we capture. Matches by GraphQL operation name (queryId is a
 * wildcard) plus the one REST follow path. Exported so the executor and
 * tests can classify without a browser.
 */
export function classifyXResponseUrl(url: string): XEventType | null {
  if (!url) return null;
  if (isFollowRestUrl(url)) return "follow";
  const op = graphqlOperationName(url);
  if (!op) return null;
  return GRAPHQL_OP_EVENTS[op] ?? null;
}

// ── JSON / form parsing helpers ──────────────────────────────────────────

function safeJsonParse(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/**
 * Depth-first walk a JSON blob looking for the first string value under
 * any of `keys`. Resilient to X's nested response shapes and to schema
 * drift (we don't hard-code paths). Returns "" if no match.
 */
function findFirstString(node: unknown, keys: readonly string[]): string {
  const wanted = new Set(keys);
  const stack: unknown[] = [node];
  while (stack.length > 0) {
    const current = stack.pop();
    if (current === null || typeof current !== "object") continue;
    if (Array.isArray(current)) {
      for (const child of current) stack.push(child);
      continue;
    }
    const obj = current as Record<string, unknown>;
    for (const [k, v] of Object.entries(obj)) {
      if (wanted.has(k) && typeof v === "string" && v) return v;
    }
    // Push children after checking keys so a shallower match wins.
    for (const v of Object.values(obj)) {
      if (v !== null && typeof v === "object") stack.push(v);
    }
  }
  return "";
}

const TWEET_ID_KEYS = ["tweet_id", "tweetId"] as const;
const REPLY_KEYS = ["reply"] as const;
const IN_REPLY_TO_KEYS = ["in_reply_to_tweet_id", "in_reply_to_status_id"] as const;
const USER_ID_KEYS = ["user_id", "userId", "id_str"] as const;

/** Pull a form-encoded field (e.g. follow's `user_id`). */
function findFormField(body: string, name: string): string {
  if (!body) return "";
  try {
    const params = new URLSearchParams(body);
    return params.get(name) ?? "";
  } catch {
    return "";
  }
}

/** Read the GraphQL `variables` JSON out of a TweetDetail GET URL. */
function variablesFromUrl(url: string): unknown {
  const qIndex = url.indexOf("?");
  if (qIndex < 0) return null;
  try {
    const params = new URLSearchParams(url.slice(qIndex + 1));
    const variables = params.get("variables");
    if (!variables) return null;
    return safeJsonParse(variables);
  } catch {
    return null;
  }
}

/**
 * Extract `{type, tweet_id}` (or `{type, user_id}` for follow) from a
 * captured request/response. Returns null when the endpoint isn't one we
 * capture or no target id is recoverable. Pure — no DOM, no side effects.
 */
export function parseXMutation(captured: CapturedXRequest): XEngagement | null {
  const { url, requestBody, responseBody } = captured;
  const type = classifyXResponseUrl(url);
  if (!type) return null;

  // Follow is a REST form-encoded POST carrying user_id (no tweet_id).
  if (type === "follow") {
    const userId =
      findFormField(requestBody, "user_id") ||
      findFirstString(safeJsonParse(responseBody), USER_ID_KEYS);
    if (!userId) return null;
    return { type, user_id: userId };
  }

  const reqJson = safeJsonParse(requestBody);

  // CreateTweet is only an engagement event when it's a reply to another
  // tweet (variables.reply.in_reply_to_tweet_id). A brand-new top-level
  // tweet is the user authoring content, not engaging — drop it.
  if (type === "comment") {
    const reply = findFirstNode(reqJson, REPLY_KEYS);
    if (reply === null) return null;
    const inReplyTo = findFirstString(reply, IN_REPLY_TO_KEYS);
    if (!inReplyTo) return null;
    return { type, tweet_id: inReplyTo };
  }

  // TweetDetail is a GET — the focalTweetId lives in the URL's `variables`.
  if (type === "view") {
    const variables = variablesFromUrl(url);
    const focal = findFirstString(variables, ["focalTweetId", "tweetId", "tweet_id"]);
    if (!focal) return null;
    return { type, tweet_id: focal };
  }

  // FavoriteTweet / CreateBookmark / CreateRetweet: tweet_id is in the
  // request variables; fall back to the response (e.g. retweet result).
  const tweetId =
    findFirstString(reqJson, TWEET_ID_KEYS) ||
    findFirstString(variablesFromUrl(url), TWEET_ID_KEYS);
  if (!tweetId) return null;
  return { type, tweet_id: tweetId };
}

/**
 * Depth-first find the first object/array value stored under any of
 * `keys` (used to locate `variables.reply`). Returns null if absent.
 */
function findFirstNode(node: unknown, keys: readonly string[]): unknown {
  const wanted = new Set(keys);
  const stack: unknown[] = [node];
  while (stack.length > 0) {
    const current = stack.pop();
    if (current === null || typeof current !== "object") continue;
    if (Array.isArray(current)) {
      for (const child of current) stack.push(child);
      continue;
    }
    const obj = current as Record<string, unknown>;
    for (const [k, v] of Object.entries(obj)) {
      if (wanted.has(k) && v !== null && typeof v === "object") return v;
    }
    for (const v of Object.values(obj)) {
      if (v !== null && typeof v === "object") stack.push(v);
    }
  }
  return null;
}

// ── MAIN-world install (observation-only) ────────────────────────────────

function emit(target: Window, engagement: XEngagement): void {
  try {
    target.postMessage(
      { source: POST_MESSAGE_SOURCE, engagement },
      target.location?.origin ?? "*",
    );
  } catch {
    // best effort — never break the page
  }
}

function urlFromInput(input: unknown): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  if (input && typeof input === "object" && "url" in input) {
    const u = (input as { url?: unknown }).url;
    return typeof u === "string" ? u : "";
  }
  return "";
}

function requestBodyFromInit(init: unknown): string {
  if (init && typeof init === "object" && "body" in init) {
    const body = (init as { body?: unknown }).body;
    if (typeof body === "string") return body;
  }
  return "";
}

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type TaggedXhr = XMLHttpRequest & { __obcXUrl?: string; __obcXBody?: string };

/**
 * Wrap `target.fetch` and `target.XMLHttpRequest` so every captured X
 * engagement / open is parsed and posted back. Observation-only: the
 * page's (input, init) are forwarded byte-identical to the original
 * fetch, and only a `Response.clone()` is read.
 *
 * Returns a disposer that restores the originals (handy for tests).
 */
export function installXTap(target: Window): () => void {
  const w = target as unknown as {
    fetch: FetchLike;
    XMLHttpRequest: { prototype: XMLHttpRequest };
  };

  // ── fetch ──────────────────────────────────────────────────────────
  const originalFetch = w.fetch;
  const wrappedFetch: FetchLike = function wrappedFetch(input, init) {
    // Forward UNCHANGED — do not touch input/init.
    const result = originalFetch.call(target, input, init);
    try {
      const url = urlFromInput(input);
      if (url && classifyXResponseUrl(url)) {
        const requestBody = requestBodyFromInit(init);
        void result
          .then((resp) => {
            // Clone so the page's own consumer reads the body untouched.
            try {
              return resp.clone().text();
            } catch {
              return "";
            }
          })
          .then((responseBody) => {
            const engagement = parseXMutation({ url, requestBody, responseBody });
            if (engagement) emit(target, engagement);
          })
          .catch(() => {
            /* swallow — never surface a rejection into the page */
          });
      }
    } catch {
      // never break the page's fetch
    }
    return result;
  };
  w.fetch = wrappedFetch;

  // ── XMLHttpRequest ─────────────────────────────────────────────────
  const proto = w.XMLHttpRequest.prototype;
  type OpenLike = (
    method: string,
    url: string | URL,
    async?: boolean,
    user?: string | null,
    password?: string | null,
  ) => void;
  type SendLike = (body?: Document | XMLHttpRequestBodyInit | null) => void;
  const originalOpen = proto.open as unknown as OpenLike;
  const originalSend = proto.send as unknown as SendLike;

  (proto as unknown as { open: OpenLike }).open = function patchedOpen(
    this: TaggedXhr,
    method: string,
    url: string | URL,
    async?: boolean,
    user?: string | null,
    password?: string | null,
  ): void {
    this.__obcXUrl = typeof url === "string" ? url : url.toString();
    return originalOpen.call(this, method, url, async ?? true, user ?? null, password ?? null);
  };

  (proto as unknown as { send: SendLike }).send = function patchedSend(
    this: TaggedXhr,
    body?: Document | XMLHttpRequestBodyInit | null,
  ): void {
    const url = this.__obcXUrl ?? "";
    if (url && classifyXResponseUrl(url)) {
      const requestBody = typeof body === "string" ? body : "";
      this.addEventListener("load", () => {
        try {
          let responseBody = "";
          if (this.responseType === "" || this.responseType === "text") {
            responseBody = this.responseText ?? "";
          } else if (this.responseType === "json" && this.response) {
            responseBody = JSON.stringify(this.response);
          }
          const engagement = parseXMutation({ url, requestBody, responseBody });
          if (engagement) emit(target, engagement);
        } catch {
          // never throw inside the XHR listener
        }
      });
    }
    // Forward UNCHANGED.
    return originalSend.call(this, body ?? null);
  };

  return (): void => {
    w.fetch = originalFetch;
    (proto as unknown as { open: OpenLike }).open = originalOpen;
    (proto as unknown as { send: SendLike }).send = originalSend;
  };
}

// Auto-install only in a real browser MAIN-world context. Guard on
// `typeof window` so node:test importing this module for the pure helpers
// doesn't wrap anything. Mirrors xhs-token-sniffer.ts.
if (typeof window !== "undefined" && typeof XMLHttpRequest !== "undefined") {
  installXTap(window);
  // eslint-disable-next-line no-console
  console.debug("[OpenBiliClaw] x graphql tap installed (MAIN world)");
}
