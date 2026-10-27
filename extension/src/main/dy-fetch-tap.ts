/**
 * Douyin MAIN-world fetch-tap.
 *
 * Pattern: install a wrapper around `window.fetch` (and
 * `XMLHttpRequest.prototype.send`) that **observes** the response
 * bodies of `/aweme/v1/web/aweme/{post,favorite,like}/` and
 * `/aweme/v1/web/user/follow/list/` calls and posts captured items
 * back to the content script via `window.postMessage`. Douyin's own
 * `webmssdk.js` has already signed the outgoing call before our
 * wrapper sees it, so we never compute X-Bogus / msToken / `_signature`
 * ourselves.
 *
 * The bundle auto-installs at document_start to observe the first page
 * request, then re-validates its wrappers after `window.byted_acrawler`
 * appears and on every explicit reinjection. This is intentional: Douyin's
 * page bundle can replace fetch/XHR after document_start, while waiting for
 * the SDK alone misses an already-completed first feed response.
 */

export type DouyinScope = "dy_post" | "dy_collect" | "dy_like" | "dy_follow";
export type DouyinSearchScope = "dy_search" | "dy_hot" | "dy_feed";

export interface DouyinBootstrapItem {
  scope: DouyinScope;
  aweme_id: string;
  creator_sec_uid: string;
  url: string;
  title: string;
  author: string;
  author_sec_uid: string;
  cover_url: string;
  view_count?: number;
  like_count?: number;
  collect_count?: number;
  comment_count?: number;
  share_count?: number;
  published_at?: string | number;
  published_label?: string;
}

export interface DouyinSearchItem {
  scope: DouyinSearchScope;
  aweme_id: string;
  url: string;
  title: string;
  author: string;
  author_sec_uid: string;
  cover_url: string;
  hot_word?: string;
  sentence_id?: string;
  seed_aweme_id?: string;
  view_count?: number;
  like_count?: number;
  collect_count?: number;
  comment_count?: number;
  share_count?: number;
  published_at?: string | number;
  published_label?: string;
}

/**
 * Map a Douyin API URL to a bootstrap scope, or null if the endpoint
 * is not one we care about. Used by both the fetch-tap to decide
 * whether to capture and by the executor to route incoming
 * postMessage events.
 *
 * Endpoint catalog cross-referenced with Johnserf-Seed/f2 (Apache-2.0,
 * read-only reference — see design doc §"Open-Source Prior Art").
 * Empirically validated against real /jingxuan landing-page traffic.
 */
export function classifyDouyinResponseUrl(url: string): DouyinScope | null {
  if (!url) return null;
  // Strip query string before matching so request_source params don't
  // disturb the path-based decision.
  const path = url.split("?", 1)[0] ?? "";
  if (path.includes("/aweme/v1/web/aweme/post/")) return "dy_post";
  if (path.includes("/aweme/v1/web/aweme/favorite/")) return "dy_collect";
  if (path.includes("/aweme/v1/web/aweme/collection/")) return "dy_collect";
  if (path.includes("/aweme/v1/web/aweme/like/")) return "dy_like";
  if (path.includes("/aweme/v1/web/user/follow/list/")) return "dy_follow";
  if (path.includes("/aweme/v1/web/user/following/list/")) return "dy_follow";
  return null;
}

function pickString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function pickNumber(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return Math.floor(value);
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value.replace(/,/g, ""));
    return Number.isFinite(parsed) ? Math.floor(parsed) : 0;
  }
  return 0;
}

function pickOptionalNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return Math.floor(value);
  if (typeof value === "string" && value.trim()) {
    const parsed = Number.parseFloat(value.replace(/,/g, ""));
    return Number.isFinite(parsed) ? Math.floor(parsed) : undefined;
  }
  return undefined;
}

function pickAwemeMetrics(rawStatistics: unknown): {
  view_count?: number;
  like_count?: number;
  collect_count?: number;
  comment_count?: number;
  share_count?: number;
} {
  if (!rawStatistics || typeof rawStatistics !== "object") return {};
  const statistics = rawStatistics as {
    play_count?: unknown;
    digg_count?: unknown;
    collect_count?: unknown;
    comment_count?: unknown;
    share_count?: unknown;
  };
  const view_count = pickNumber(statistics.play_count);
  const like_count = pickNumber(statistics.digg_count);
  const collect_count = pickNumber(statistics.collect_count);
  const comment_count = pickNumber(statistics.comment_count);
  const share_count = pickNumber(statistics.share_count);
  return {
    ...(view_count > 0 ? { view_count } : {}),
    ...(like_count > 0 ? { like_count } : {}),
    ...(collect_count > 0 ? { collect_count } : {}),
    ...(comment_count > 0 ? { comment_count } : {}),
    ...(share_count > 0 ? { share_count } : {}),
  };
}

function pickFirstUrl(coverField: unknown): string {
  if (!coverField || typeof coverField !== "object") return "";
  const cover = coverField as { url_list?: unknown };
  if (!Array.isArray(cover.url_list)) return "";
  const first = cover.url_list.find((u) => typeof u === "string" && u);
  return typeof first === "string" ? first : "";
}

function pickAuthor(awemeAuthor: unknown): { nickname: string; sec_uid: string } {
  if (!awemeAuthor || typeof awemeAuthor !== "object") return { nickname: "", sec_uid: "" };
  const a = awemeAuthor as { nickname?: unknown; sec_uid?: unknown };
  return {
    nickname: pickString(a.nickname),
    sec_uid: pickString(a.sec_uid),
  };
}

/**
 * Parse a `/aweme/v1/web/aweme/{post,favorite,like}/` response into
 * normalized items. Tolerates missing `aweme_list`, wrong types,
 * and individual-row malformations (drops the bad row, keeps the rest).
 *
 * Field shape reference:
 * - `aweme_id`: stable id, used as identity key
 * - `desc` / `preview_title`: title (real /aweme/v2/web/module/feed/
 *   samples shipped preview_title alongside a blank desc — accept both)
 * - `author.nickname` / `author.sec_uid`: creator
 * - `video.cover.url_list[]`: cover image candidates
 */
export function parseAwemeListResponse(
  json: unknown,
  scope: DouyinScope,
): DouyinBootstrapItem[] {
  if (!json || typeof json !== "object") return [];
  const root = json as { aweme_list?: unknown };
  if (!Array.isArray(root.aweme_list)) return [];

  const items: DouyinBootstrapItem[] = [];
  for (const raw of root.aweme_list) {
    if (!raw || typeof raw !== "object") continue;
    const aweme = raw as {
      aweme_id?: unknown;
      create_time?: unknown;
      desc?: unknown;
      preview_title?: unknown;
      author?: unknown;
      video?: { cover?: unknown };
      statistics?: unknown;
    };
    const awemeId = pickString(aweme.aweme_id);
    const title = pickString(aweme.desc) || pickString(aweme.preview_title);
    if (!awemeId && !title) continue;
    const author = pickAuthor(aweme.author);
    const coverUrl = pickFirstUrl(aweme.video?.cover);
    const publishedAt = pickOptionalNumber(aweme.create_time);
    items.push({
      scope,
      aweme_id: awemeId,
      creator_sec_uid: "",
      url: awemeId ? `https://www.douyin.com/video/${awemeId}` : "",
      title,
      author: author.nickname,
      author_sec_uid: author.sec_uid,
      cover_url: coverUrl,
      ...pickAwemeMetrics(aweme.statistics),
      ...(publishedAt !== undefined ? { published_at: publishedAt } : {}),
    });
  }
  return items;
}

/**
 * Parse a `/aweme/v1/web/user/follow/list/` response into normalized
 * items. Accepts both `followings` and `follow_list` as the array key
 * since f2 references show the variant has shifted historically.
 */
export function parseUserFollowListResponse(json: unknown): DouyinBootstrapItem[] {
  if (!json || typeof json !== "object") return [];
  const root = json as { followings?: unknown; follow_list?: unknown };
  const list = Array.isArray(root.followings)
    ? root.followings
    : Array.isArray(root.follow_list)
      ? root.follow_list
      : null;
  if (!list) return [];

  const items: DouyinBootstrapItem[] = [];
  for (const raw of list) {
    if (!raw || typeof raw !== "object") continue;
    const creator = raw as {
      sec_uid?: unknown;
      nickname?: unknown;
      avatar_thumb?: unknown;
    };
    const secUid = pickString(creator.sec_uid);
    if (!secUid) continue;
    const nickname = pickString(creator.nickname);
    const avatarUrl = pickFirstUrl(creator.avatar_thumb);
    items.push({
      scope: "dy_follow",
      aweme_id: "",
      creator_sec_uid: secUid,
      url: `https://www.douyin.com/user/${secUid}`,
      title: nickname,
      author: nickname,
      author_sec_uid: secUid,
      cover_url: avatarUrl,
    });
  }
  return items;
}

interface HotRelatedMeta {
  word?: string;
  sentenceId?: string;
  seedAwemeId?: string;
}

function normalizeSearchAweme(
  raw: unknown,
  scope: DouyinSearchScope = "dy_search",
  meta: HotRelatedMeta = {},
): DouyinSearchItem | null {
  if (!raw || typeof raw !== "object") return null;
  const aweme = raw as {
    aweme_id?: unknown;
    create_time?: unknown;
    desc?: unknown;
    preview_title?: unknown;
    share_info?: { share_title?: unknown; share_desc?: unknown };
    author?: unknown;
    video?: { cover?: unknown; origin_cover?: unknown; animated_cover?: unknown };
    statistics?: unknown;
  };
  const awemeId = pickString(aweme.aweme_id);
  const title =
    pickString(aweme.desc) ||
    pickString(aweme.preview_title) ||
    pickString(aweme.share_info?.share_title) ||
    pickString(aweme.share_info?.share_desc);
  if (!awemeId && !title) return null;
  const author = pickAuthor(aweme.author);
  const publishedAt = pickOptionalNumber(aweme.create_time);
  const item: DouyinSearchItem = {
    scope,
    aweme_id: awemeId,
    url: awemeId ? `https://www.douyin.com/video/${awemeId}` : "",
    title,
    author: author.nickname,
    author_sec_uid: author.sec_uid,
    cover_url:
      pickFirstUrl(aweme.video?.cover) ||
      pickFirstUrl(aweme.video?.origin_cover) ||
      pickFirstUrl(aweme.video?.animated_cover),
    ...pickAwemeMetrics(aweme.statistics),
    ...(publishedAt !== undefined ? { published_at: publishedAt } : {}),
  };
  if (scope === "dy_hot") {
    item.hot_word = meta.word ?? "";
    item.sentence_id = meta.sentenceId ?? "";
    item.seed_aweme_id = meta.seedAwemeId ?? "";
  }
  return item;
}

export function parseSearchAwemeResponse(json: unknown): DouyinSearchItem[] {
  if (!json || typeof json !== "object") return [];
  const root = json as { data?: unknown; aweme_list?: unknown };
  const rawRows = Array.isArray(root.aweme_list)
    ? root.aweme_list
    : Array.isArray(root.data)
      ? root.data
      : [];
  const items: DouyinSearchItem[] = [];
  const seen = new Set<string>();
  for (const row of rawRows) {
    if (!row || typeof row !== "object") continue;
    const record = row as { aweme_info?: unknown; item?: unknown };
    const normalized = normalizeSearchAweme(record.aweme_info ?? record.item ?? record);
    if (!normalized) continue;
    const key = normalized.aweme_id || `${normalized.title}:${normalized.author}`;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    items.push(normalized);
  }
  return items;
}

export function parseRelatedAwemeResponse(
  json: unknown,
  meta: HotRelatedMeta = {},
): DouyinSearchItem[] {
  if (!json || typeof json !== "object") return [];
  const root = json as { aweme_list?: unknown };
  if (!Array.isArray(root.aweme_list)) return [];
  const items: DouyinSearchItem[] = [];
  const seen = new Set<string>();
  for (const raw of root.aweme_list) {
    const normalized = normalizeSearchAweme(raw, "dy_hot", meta);
    if (!normalized) continue;
    const key = normalized.aweme_id || `${normalized.title}:${normalized.author}`;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    items.push(normalized);
  }
  return items;
}

export function parseFeedAwemeResponse(json: unknown): DouyinSearchItem[] {
  if (!json || typeof json !== "object") return [];
  const root = json as { aweme_list?: unknown; data?: unknown };
  const rawRows = Array.isArray(root.aweme_list)
    ? root.aweme_list
    : Array.isArray(root.data)
      ? root.data
      : [];
  const items: DouyinSearchItem[] = [];
  const seen = new Set<string>();
  for (const row of rawRows) {
    if (!row || typeof row !== "object") continue;
    const record = row as { aweme_info?: unknown; item?: unknown; aweme?: unknown };
    const normalized = normalizeSearchAweme(
      record.aweme_info ?? record.item ?? record.aweme ?? record,
      "dy_feed",
    );
    if (!normalized) continue;
    if (!normalized.title && !normalized.author && !normalized.cover_url) continue;
    const key = normalized.aweme_id || `${normalized.title}:${normalized.author}`;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    items.push(normalized);
  }
  return items;
}

/**
 * Poll `target.byted_acrawler` until it appears or the timeout elapses.
 * Resolves true on appearance, false on timeout. The 50ms poll
 * cadence is fine: the SDK is loaded by a synchronous script tag
 * relatively early, and a real installer typically waits 200-1500ms
 * before resolving.
 */
export async function waitForDouyinSdk(
  target: Window,
  timeoutMs: number,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  // Cast through unknown to touch the SDK-bearing field on Window.
  const t = target as unknown as { byted_acrawler?: unknown };
  while (Date.now() < deadline) {
    if (t.byted_acrawler) return true;
    await new Promise((r) => setTimeout(r, 50));
  }
  return Boolean(t.byted_acrawler);
}

type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

type XhrOpenLike = (
  method: string,
  url: string | URL,
  async?: boolean,
  user?: string | null,
  password?: string | null,
) => void;

interface FetchTapInstallState {
  originalFetch: FetchLike;
  wrappedFetch: FetchLike;
}

interface XhrTapInstallState {
  prototype: XMLHttpRequest;
  originalOpen: XhrOpenLike;
  wrappedOpen: XhrOpenLike;
}

interface ActiveApiRequest {
  token: object;
  timeoutId: ReturnType<typeof setTimeout>;
}

interface ApiHarvesterInstallState {
  listener: EventListener;
  activeRequests: Map<string, ActiveApiRequest>;
  requestTimeoutMs: number;
}

interface DouyinMainBridgeState {
  fetchTap?: FetchTapInstallState;
  xhrTap?: XhrTapInstallState;
  apiHarvester?: ApiHarvesterInstallState;
  autoInstallStatus?: "pending" | "installed";
}

type DouyinMainBridgeWindow = Window & {
  __openbiliclawDouyinMainBridgeState?: DouyinMainBridgeState;
};

/**
 * Store install state on the page Window, rather than in module globals.
 * Chrome executes each scripting.executeScript file injection in a fresh
 * bundle closure, while the MAIN-world Window survives SPA route changes and
 * repeated injections. The Window marker therefore makes installation
 * idempotent across both calls and separately evaluated bundle instances.
 */
function getDouyinMainBridgeState(target: Window): DouyinMainBridgeState {
  const shared = target as DouyinMainBridgeWindow;
  shared.__openbiliclawDouyinMainBridgeState ??= {};
  return shared.__openbiliclawDouyinMainBridgeState;
}

const SEARCH_TAP_MESSAGE_TYPE = "OPENBILICLAW_DOUYIN_SEARCH_PAGE";

function isSearchResponseUrl(url: string): boolean {
  if (!url) return false;
  const path = url.split("?", 1)[0] ?? "";
  return (
    path.includes("/aweme/v1/web/general/search/single/") ||
    path.includes("/aweme/v1/web/general/search/stream/") ||
    path.includes("/aweme/v1/web/search/item/")
  );
}

function isPassiveDiscoveryResponseUrl(url: string): boolean {
  return classifyPassiveDiscoveryScope(url) !== null;
}

function classifyPassiveDiscoveryScope(url: string): DouyinSearchScope | null {
  if (!url) return null;
  const path = url.split("?", 1)[0] ?? "";
  if (isSearchResponseUrl(url)) return "dy_search";
  if (path.includes("/aweme/v1/web/aweme/related/")) return "dy_hot";
  if (
    path.includes("/aweme/v1/web/tab/feed/") ||
    path.includes("/aweme/v2/web/module/feed/")
  ) {
    return "dy_feed";
  }
  return null;
}

function parsePassiveDiscoveryResponse(url: string, json: unknown): DouyinSearchItem[] {
  const path = url.split("?", 1)[0] ?? "";
  if (path.includes("/aweme/v1/web/aweme/related/")) {
    return parseRelatedAwemeResponse(json);
  }
  if (path.includes("/aweme/v1/web/tab/feed/") || path.includes("/aweme/v2/web/module/feed/")) {
    return parseFeedAwemeResponse(json);
  }
  if (isSearchResponseUrl(url)) {
    return parseSearchAwemeResponse(json);
  }
  return [];
}

function isSuccessfulDouyinResponsePayload(json: unknown): boolean {
  if (!json || typeof json !== "object" || Array.isArray(json)) return false;
  const root = json as Record<string, unknown>;
  if (!("status_code" in root)) return true;
  const statusCode = Number(root.status_code);
  return Number.isFinite(statusCode) && statusCode === 0;
}

function isValidPassiveDiscoveryEnvelope(
  scope: DouyinSearchScope,
  json: unknown,
): boolean {
  if (!isSuccessfulDouyinResponsePayload(json)) return false;
  const root = json as Record<string, unknown>;
  if (scope === "dy_search") {
    return Array.isArray(root.data) || Array.isArray(root.aweme_list);
  }
  if (scope === "dy_hot") return Array.isArray(root.aweme_list);
  return Array.isArray(root.aweme_list) || Array.isArray(root.data);
}

function parseJsonText(text: string): unknown {
  const trimmed = text.trim();
  if (!trimmed) throw new SyntaxError("empty JSON body");
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    const withoutChunkPrefix = trimmed.replace(/^[0-9a-fA-F]+\r?\n/, "").trim();
    try {
      return JSON.parse(withoutChunkPrefix) as unknown;
    } catch {
      const start = withoutChunkPrefix.search(/[{\[]/);
      const objectEnd = withoutChunkPrefix.lastIndexOf("}");
      const arrayEnd = withoutChunkPrefix.lastIndexOf("]");
      const end = Math.max(objectEnd, arrayEnd);
      if (start < 0 || end < start) throw new SyntaxError("invalid JSON body");
      return JSON.parse(withoutChunkPrefix.slice(start, end + 1)) as unknown;
    }
  }
}

async function readJsonResponse(resp: Response): Promise<unknown> {
  try {
    return (await resp.clone().json()) as unknown;
  } catch {
    const text = await resp.clone().text();
    return parseJsonText(text);
  }
}

/** Observe complete JSON frames without waiting for a search stream to end. */
async function observeSearchStream(
  response: Response, onFrame: (frame: unknown) => void,
): Promise<void> {
  const body = response.clone().body;
  if (!body) return;
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let scan = 0;
  let start = -1;
  let depth = 0;
  let inString = false;
  let escaped = false;
  let bytes = 0;
  // Observation alone is bounded. Cancel only this clone's reader; the
  // website retains its original Response and controls its own request.
  const timer = setTimeout(() => { void reader.cancel().catch(() => {}); }, 60_000);
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 4 * 1024 * 1024) break;
      buffer += decoder.decode(value, { stream: true });
      for (; scan < buffer.length; scan += 1) {
        const char = buffer[scan];
        if (start < 0) {
          if (char === "{" || char === "[") {
            start = scan;
            depth = 1;
            inString = false;
            escaped = false;
          }
          continue;
        }
        if (inString) {
          if (escaped) escaped = false;
          else if (char === "\\") escaped = true;
          else if (char === '"') inString = false;
          continue;
        }
        if (char === '"') inString = true;
        else if (char === "{" || char === "[") depth += 1;
        else if (char === "}" || char === "]") depth -= 1;
        if (depth !== 0) continue;
        try { onFrame(JSON.parse(buffer.slice(start, scan + 1))); } catch {
          // Ignore malformed frames without affecting the page's consumer.
        }
        start = -1;
      }
      if (start < 0) {
        buffer = "";
        scan = 0;
      } else if (start > 0) {
        buffer = buffer.slice(start);
        scan -= start;
        start = 0;
      }
    }
  } finally {
    clearTimeout(timer);
    void reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

/**
 * Install the fetch-tap onto `target.fetch`. Wraps whatever
 * `target.fetch` is at install time, which in production is the
 * SDK's already-installed wrapper (see waitForDouyinSdk above).
 *
 * The callback runs on every captured response. The fetch-tap never
 * mutates the original Response — we use `Response.clone()` so the
 * page's own consumer reads the body untouched.
 *
 * Returns a disposer that restores the original `target.fetch`. A duplicate
 * installation returns a no-op disposer because the first installer owns the
 * shared wrapper.
 */
export function installFetchTap(
  target: Window,
  postBack: (items: DouyinBootstrapItem[], scope: DouyinScope) => void,
  postSearchBack?: (items: DouyinSearchItem[], scope: DouyinSearchScope) => void,
): () => void {
  const w = target as unknown as { fetch: FetchLike };
  const shared = getDouyinMainBridgeState(target);
  const installed = shared.fetchTap;
  if (installed && w.fetch === installed.wrappedFetch) {
    // The first installer owns the live wrapper. A duplicate caller gets a
    // no-op disposer so it cannot accidentally tear down that installation.
    return (): void => {};
  }
  const originalFetch = w.fetch;

  const wrapped: FetchLike = async (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : (input as Request).url;
    const resp = await originalFetch(input, init);
    const scope = classifyDouyinResponseUrl(url);
    if (scope) {
      try {
        const json: unknown = await readJsonResponse(resp);
        const items =
          scope === "dy_follow"
            ? parseUserFollowListResponse(json)
            : parseAwemeListResponse(json, scope);
        if (items.length > 0) {
          postBack(items, scope);
        }
      } catch {
        // Body wasn't JSON or already consumed — silent skip is the
        // right move; we never want to throw inside fetch-tap because
        // the page's React app would observe the rejection.
      }
    } else if (resp.ok && isPassiveDiscoveryResponseUrl(url) && postSearchBack) {
      if (url.split("?", 1)[0]?.includes("/general/search/stream/")) {
        // Never hold the native fetch promise until EOF. The page must be
        // able to render its own chunks while the tap observes its clone.
        void observeSearchStream(resp, json => {
          if (isValidPassiveDiscoveryEnvelope("dy_search", json)) {
            postSearchBack(parseSearchAwemeResponse(json), "dy_search");
          }
        }).catch(() => {});
        return resp;
      }
      try {
        const json: unknown = await readJsonResponse(resp);
        const discoveryScope = classifyPassiveDiscoveryScope(url);
        if (discoveryScope && isValidPassiveDiscoveryEnvelope(discoveryScope, json)) {
          postSearchBack(parsePassiveDiscoveryResponse(url, json), discoveryScope);
        }
      } catch {
        // Best-effort search observation; never disturb the page.
      }
    }
    return resp;
  };

  const state: FetchTapInstallState = {
    originalFetch,
    wrappedFetch: wrapped,
  };
  shared.fetchTap = state;
  w.fetch = wrapped;
  return (): void => {
    if (shared.fetchTap !== state) return;
    if (w.fetch === wrapped) w.fetch = originalFetch;
    delete shared.fetchTap;
  };
}

/**
 * Install an XHR tap parallel to the fetch tap. Douyin's older code
 * paths (and some user-tab endpoints) use XMLHttpRequest, which the
 * fetch wrap can't see. We hook .open() to capture the URL, then
 * listen on the per-request readystatechange (state=4) and parse
 * .responseText.
 *
 * Diagnostic-only: returns the disposer that un-wraps .open. A duplicate
 * installation returns a no-op disposer owned independently of the live tap.
 */
export function installXhrTap(
  target: Window,
  postBack: (items: DouyinBootstrapItem[], scope: DouyinScope) => void,
  postSearchBack?: (items: DouyinSearchItem[], scope: DouyinSearchScope) => void,
): () => void {
  const proto = (target as unknown as { XMLHttpRequest: { prototype: XMLHttpRequest } })
    .XMLHttpRequest.prototype;
  const shared = getDouyinMainBridgeState(target);
  const installed = shared.xhrTap;
  if (installed && installed.prototype === proto && proto.open === installed.wrappedOpen) {
    return (): void => {};
  }
  const originalOpen = proto.open as unknown as XhrOpenLike;

  const wrappedOpen: XhrOpenLike = function wrappedOpen(
    this: XMLHttpRequest,
    method: string,
    url: string | URL,
    async?: boolean,
    user?: string | null,
    password?: string | null,
  ) {
    const urlString = typeof url === "string" ? url : url.toString();
    (this as unknown as { __obcUrl?: string }).__obcUrl = urlString;
    this.addEventListener("readystatechange", () => {
      if (this.readyState !== 4) return;
      const u = (this as unknown as { __obcUrl?: string }).__obcUrl ?? urlString;
      const scope = classifyDouyinResponseUrl(u);
      if (!scope && !isPassiveDiscoveryResponseUrl(u)) return;
      try {
        const httpStatus = Number(this.status);
        if (Number.isFinite(httpStatus) && httpStatus > 0 && (httpStatus < 200 || httpStatus >= 300)) {
          return;
        }
        const text = this.responseText;
        if (!text) return;
        const json: unknown = parseJsonText(text);
        if (scope) {
          const items =
            scope === "dy_follow"
              ? parseUserFollowListResponse(json)
              : parseAwemeListResponse(json, scope);
          if (items.length > 0) postBack(items, scope);
          return;
        }
        const discoveryScope = classifyPassiveDiscoveryScope(u);
        if (
          postSearchBack &&
          discoveryScope &&
          isValidPassiveDiscoveryEnvelope(discoveryScope, json)
        ) {
          postSearchBack(parsePassiveDiscoveryResponse(u, json), discoveryScope);
        }
      } catch {
        // Best-effort: never throw inside XHR listener.
      }
    });
    return originalOpen.call(this, method, url, async ?? true, user, password);
  };

  const state: XhrTapInstallState = {
    prototype: proto,
    originalOpen,
    wrappedOpen,
  };
  shared.xhrTap = state;
  (proto as unknown as { open: XhrOpenLike }).open = wrappedOpen;
  return (): void => {
    if (shared.xhrTap !== state) return;
    if (proto.open === wrappedOpen) {
      (proto as unknown as { open: XhrOpenLike }).open = originalOpen;
    }
    delete shared.xhrTap;
  };
}

// ---------------------------------------------------------------------------
// Auto-install when loaded as a content_scripts MAIN-world script
// ---------------------------------------------------------------------------
//
// Side-effect block guarded by ``typeof window !== "undefined"`` so
// node:test importing the module for pure-helper tests doesn't trigger
// any real installation. Mirrors the xhs-state-bridge.ts pattern.

const FETCH_TAP_MESSAGE_TYPE = "OPENBILICLAW_DOUYIN_AWEME_PAGE";
// Install-status sentinel: MAIN world emits one of these on install
// resolve so the isolated-world content script can tell whether the
// fetch-tap successfully wrapped page-bundle fetch (status="installed")
// or whether SDK detection timed out (status="skipped_no_sdk"). Used
// for diagnosing scope_status=empty results — without this we can't
// tell "captured 0 because SDK never loaded" from "captured 0 because
// risk-control empty-200'd everything".
const FETCH_TAP_INSTALL_TYPE = "OPENBILICLAW_DOUYIN_FETCH_TAP_INSTALL";

/**
 * Replay an install-status ping a few times, spaced apart, so an
 * isolated-world content script that registered its listener slightly
 * after MAIN-world install resolved still catches one. Defensive
 * against the race we observed in the 2026-05-08 e2e probe.
 *
 * Three pings × 500ms apart covers:
 *   - content script at document_start (catches first ping at T+0)
 *   - content script at document_idle (catches third ping at T+1000ms)
 *   - any unexpected delay short of 1.5s
 */
function replayInstallStatusPing(
  target: Window,
  status: "installed" | "skipped_no_sdk",
): void {
  const fire = (): void => {
    target.postMessage({ type: FETCH_TAP_INSTALL_TYPE, status }, target.location.origin);
  };
  fire();
  setTimeout(fire, 500);
  setTimeout(fire, 1_000);
}

// ---------------------------------------------------------------------------
// API-driven harvester — Douyin user-tab endpoints, cursor pagination
// ---------------------------------------------------------------------------
//
// Replaces UI-scrolling for scope harvest. The MAIN-world fetch is
// already wrapped by webmssdk.js (waitForDouyinSdk above), so calls
// to window.fetch get X-Bogus / a_bogus / msToken auto-signed.
//
// Endpoints + cursor key per F2 (Apache-2.0 reference):
//   dy_post:    /aweme/v1/web/aweme/post/      max_cursor / has_more
//   dy_collect: /aweme/v1/web/aweme/favorite/  max_cursor / has_more
//   dy_like:    /aweme/v1/web/aweme/like/      max_cursor / has_more
//   dy_follow:  /aweme/v1/web/user/follow/list/  max_time / has_more
//
// Isolated-world content script invokes this via postMessage:
//   request:  { type: "OPENBILICLAW_DOUYIN_API_REQUEST",
//               requestId, scope, secUid, maxItems }
//   response: { type: "OPENBILICLAW_DOUYIN_API_RESPONSE",
//               requestId, items, error?, pages_fetched }

const API_REQUEST_TYPE = "OPENBILICLAW_DOUYIN_API_REQUEST";
const API_RESPONSE_TYPE = "OPENBILICLAW_DOUYIN_API_RESPONSE";
const IDENTITY_REQUEST_TYPE = "OPENBILICLAW_DOUYIN_IDENTITY_REQUEST";
const IDENTITY_RESPONSE_TYPE = "OPENBILICLAW_DOUYIN_IDENTITY_RESPONSE";
const SEARCH_API_REQUEST_TYPE = "OPENBILICLAW_DOUYIN_SEARCH_API_REQUEST";
const SEARCH_API_RESPONSE_TYPE = "OPENBILICLAW_DOUYIN_SEARCH_API_RESPONSE";
const HOT_API_REQUEST_TYPE = "OPENBILICLAW_DOUYIN_HOT_API_REQUEST";
const HOT_API_RESPONSE_TYPE = "OPENBILICLAW_DOUYIN_HOT_API_RESPONSE";
const FEED_API_REQUEST_TYPE = "OPENBILICLAW_DOUYIN_FEED_API_REQUEST";
const FEED_API_RESPONSE_TYPE = "OPENBILICLAW_DOUYIN_FEED_API_RESPONSE";

const SCOPE_ENDPOINT: Record<DouyinScope, string> = {
  dy_post: "/aweme/v1/web/aweme/post/",
  dy_collect: "/aweme/v1/web/aweme/favorite/",
  dy_like: "/aweme/v1/web/aweme/like/",
  dy_follow: "/aweme/v1/web/user/follow/list/",
};

function buildScopeApiUrl(
  scope: DouyinScope,
  secUid: string,
  cursor: string,
): string {
  const params = new URLSearchParams({
    device_platform: "webapp",
    aid: "6383",
    channel: "channel_pc_web",
    pc_client_type: "1",
    sec_user_id: secUid,
    count: scope === "dy_follow" ? "20" : "18",
    publish_video_strategy_type: "2",
    update_version_code: "170400",
    version_code: "170400",
    version_name: "17.4.0",
    cookie_enabled: "true",
  });
  if (scope === "dy_follow") {
    params.set("max_time", String(cursor));
    params.set("min_time", "0");
    params.set("with_fstatus", "1");
    params.set("source_type", "1");
  } else {
    params.set("max_cursor", String(cursor));
    params.set("min_cursor", "0");
    params.set("whale_cut_token", "");
    params.set("cut_version", "1");
  }
  return `${SCOPE_ENDPOINT[scope]}?${params.toString()}`;
}

interface ScopeApiResult {
  items: DouyinBootstrapItem[];
  pages_fetched: number;
  error?: string;
}

interface SearchApiResult {
  items: DouyinSearchItem[];
  pages_fetched: number;
  error?: string;
}

const SCOPE_API_MAX_PAGES = 50;
const SCOPE_API_PAGE_DELAY_MS = 300;

function normalizeScopeHasMore(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (value === 0 || value === "0") return false;
  if (value === 1 || value === "1") return true;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (normalized === "0" || normalized === "false") return false;
    if (normalized === "1" || normalized === "true") return true;
  }
  return null;
}

function scopeApiStatusError(root: Record<string, unknown>): string {
  if (!Object.prototype.hasOwnProperty.call(root, "status_code")) return "";
  const raw = root.status_code;
  let normalized: string | null = null;
  if (typeof raw === "number" && Number.isSafeInteger(raw)) {
    normalized = String(raw);
  } else if (typeof raw === "string" && /^-?\d+$/.test(raw.trim())) {
    const trimmed = raw.trim();
    normalized = /^-?0+$/.test(trimmed)
      ? "0"
      : trimmed.replace(/^(-?)0+(?=\d)/, "$1");
  }
  if (normalized === "0") return "";
  return normalized === null ? "api_status_invalid" : `api_status_${normalized}`;
}

function normalizeScopeCursor(value: unknown): string | null {
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value) || value < 0) return null;
    return String(value);
  }
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  return trimmed.replace(/^0+(?=\d)/, "");
}

function scopeCursorFields(scope: DouyinScope): readonly string[] {
  // source_type=1 follow responses traditionally advance via min_time.
  // Some page builds instead expose the same continuation as max_time or
  // cursor, so accept those aliases without weakening validation.
  return scope === "dy_follow"
    ? ["min_time", "max_time", "cursor"]
    : ["max_cursor", "cursor"];
}

function pickNextScopeCursor(
  root: Record<string, unknown>,
  scope: DouyinScope,
  currentCursor: string,
  requestedCursors: ReadonlySet<string>,
): { cursor?: string; error?: string } {
  let present = false;
  let valid = false;
  let repeatedCurrent = false;
  let repeatedEarlier = false;

  for (const field of scopeCursorFields(scope)) {
    if (!Object.prototype.hasOwnProperty.call(root, field)) continue;
    present = true;
    const candidate = normalizeScopeCursor(root[field]);
    if (candidate === null) continue;
    valid = true;
    if (candidate === currentCursor) {
      repeatedCurrent = true;
      continue;
    }
    if (requestedCursors.has(candidate)) {
      repeatedEarlier = true;
      continue;
    }
    return { cursor: candidate };
  }

  if (!present) return { error: "pagination_cursor_missing" };
  if (!valid) return { error: "pagination_cursor_invalid" };
  if (repeatedCurrent) return { error: "pagination_cursor_not_advanced" };
  if (repeatedEarlier) return { error: "pagination_cursor_cycle" };
  return { error: "pagination_cursor_invalid" };
}

export async function harvestScopeViaApi(
  target: Window,
  scope: DouyinScope,
  secUid: string,
  maxItems: number,
  pageDelayMs: number = SCOPE_API_PAGE_DELAY_MS,
): Promise<ScopeApiResult> {
  const w = target as unknown as { fetch: FetchLike };
  const items: DouyinBootstrapItem[] = [];
  const seen = new Set<string>();
  let cursor = "0";
  const requestedCursors = new Set<string>([cursor]);
  let pages = 0;
  let error = "";
  const cap = Math.max(0, Math.floor(maxItems));
  for (let page = 0; page < SCOPE_API_MAX_PAGES && items.length < cap; page += 1) {
    const url = buildScopeApiUrl(scope, secUid, cursor);
    let json: unknown;
    try {
      const resp = await w.fetch(url, { credentials: "include" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      json = (await resp.json()) as unknown;
    } catch (err) {
      if (page === 0) throw err;
      error = String(err instanceof Error ? err.message : err);
      break;
    }
    pages += 1;
    if (!json || typeof json !== "object" || Array.isArray(json)) {
      error = "api_response_invalid";
      break;
    }
    const root = json as Record<string, unknown>;
    const statusError = scopeApiStatusError(root);
    if (statusError) {
      error = statusError;
      break;
    }
    const batch =
      scope === "dy_follow"
        ? parseUserFollowListResponse(json)
        : parseAwemeListResponse(json, scope);
    for (const item of batch) {
      const key = scope === "dy_follow" ? item.creator_sec_uid : item.aweme_id;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      items.push(item);
      if (items.length >= cap) break;
    }
    if (!Object.prototype.hasOwnProperty.call(root, "has_more")) {
      error = "pagination_has_more_missing";
      break;
    }
    const hasMore = normalizeScopeHasMore(root.has_more);
    if (hasMore === null) {
      error = "pagination_has_more_invalid";
      break;
    }
    if (!hasMore || items.length >= cap) break;
    if (page + 1 >= SCOPE_API_MAX_PAGES) {
      error = "pagination_page_limit_reached";
      break;
    }

    const next = pickNextScopeCursor(root, scope, cursor, requestedCursors);
    if (!next.cursor) {
      error = next.error ?? "pagination_cursor_invalid";
      break;
    }
    cursor = next.cursor;
    requestedCursors.add(cursor);
    if (pageDelayMs > 0) {
      await new Promise((r) => setTimeout(r, pageDelayMs));
    }
  }
  return {
    items,
    pages_fetched: pages,
    ...(error ? { error } : {}),
  };
}

/**
 * Extract the account-scoped identifier from the verified
 * ``/aweme/v1/web/user/profile/self/`` response.
 *
 * The status-code check is deliberate: guest sessions can still return HTTP
 * 200, but Douyin answers them with ``status_code=8``. Only the authoritative
 * success shape is accepted as the current account identity.
 */
export function extractDouyinSelfSecUid(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const root = payload as Record<string, unknown>;
  if (root.status_code !== 0) return "";
  const user = root.user;
  if (!user || typeof user !== "object") return "";
  const record = user as Record<string, unknown>;
  const value = record.sec_uid ?? record.secUid;
  return typeof value === "string" ? value.trim() : "";
}

/**
 * Resolve the current account identity through the page's MAIN-world fetch.
 * This is the same read-only endpoint used by the backend login probe, but
 * running here preserves the browser's live cookie and signing context.
 */
export async function fetchDouyinSelfSecUid(target: Window): Promise<string> {
  const w = target as unknown as { fetch: FetchLike };
  const url = `${target.location.origin}/aweme/v1/web/user/profile/self/`;
  const resp = await w.fetch(url, { credentials: "include" });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return extractDouyinSelfSecUid((await resp.json()) as unknown);
}

type DouyinIdentityWindow = Window & {
  __openbiliclawDouyinSelfSecUid?: string;
  __openbiliclawDouyinSelfSecUidPromise?: Promise<string>;
};

async function resolveDouyinSelfSecUid(target: Window): Promise<string> {
  const shared = target as DouyinIdentityWindow;
  if (shared.__openbiliclawDouyinSelfSecUid) {
    return shared.__openbiliclawDouyinSelfSecUid;
  }
  if (shared.__openbiliclawDouyinSelfSecUidPromise) {
    return await shared.__openbiliclawDouyinSelfSecUidPromise;
  }

  const pending = fetchDouyinSelfSecUid(target).then((secUid) => {
    if (secUid) shared.__openbiliclawDouyinSelfSecUid = secUid;
    return secUid;
  });
  shared.__openbiliclawDouyinSelfSecUidPromise = pending;
  try {
    return await pending;
  } finally {
    if (shared.__openbiliclawDouyinSelfSecUidPromise === pending) {
      delete shared.__openbiliclawDouyinSelfSecUidPromise;
    }
  }
}

function pickCookieValue(cookieHeader: string, name: string): string {
  const prefix = `${name}=`;
  for (const part of cookieHeader.split(";")) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) return trimmed.slice(prefix.length);
  }
  return "";
}

function parseChromeVersion(userAgent: string): string {
  const match = userAgent.match(/(?:Chrome|Chromium)\/([\d.]+)/);
  return match?.[1] ?? "131.0.0.0";
}

function buildSearchApiUrl(
  target: Window,
  path: string,
  keyword: string,
  offset: number,
  count: number,
): string {
  const nav = target.navigator;
  const chromeVersion = parseChromeVersion(nav?.userAgent ?? "");
  const platform = nav?.platform || "Win32";
  const isMac = /Mac/i.test(platform);
  const params = new URLSearchParams({
    device_platform: "webapp",
    aid: "6383",
    channel: "channel_pc_web",
    pc_client_type: "1",
    version_code: "290100",
    version_name: "29.1.0",
    cookie_enabled: "true",
    screen_width: String(target.screen?.width ?? 1920),
    screen_height: String(target.screen?.height ?? 1080),
    browser_language: nav?.language || "zh-CN",
    browser_platform: platform,
    browser_name: "Chrome",
    browser_version: chromeVersion,
    browser_online: String(nav?.onLine ?? true),
    engine_name: "Blink",
    engine_version: chromeVersion,
    os_name: isMac ? "Mac OS" : "Windows",
    os_version: isMac ? "10.15.7" : "10",
    platform: "PC",
    msToken: pickCookieValue(target.document?.cookie ?? "", "msToken"),
    keyword,
    search_source: "normal_search",
    query_correct_type: "1",
    is_filter_search: "0",
    offset: String(offset),
    count: String(count),
  });
  if (path.includes("/general/search/single/")) {
    params.set("search_channel", "aweme_video_web");
  }
  return `${target.location.origin}${path}?${params.toString()}`;
}

type DouyinAcrawler = {
  frontierSign?: (input: { url: string } | string) => unknown;
};

function applyDouyinApiSignature(target: Window, url: string): string {
  const acrawler = (target as unknown as { byted_acrawler?: DouyinAcrawler }).byted_acrawler;
  if (typeof acrawler?.frontierSign !== "function") return url;

  let signed: unknown;
  try {
    signed = acrawler.frontierSign({ url });
  } catch {
    return url;
  }
  if (!signed) return url;
  if (typeof signed === "string") {
    if (/^https?:\/\//.test(signed) || signed.startsWith("/")) return signed;
    const parsed = new URL(url);
    if (signed.includes("=")) {
      const params = new URLSearchParams(signed.replace(/^[?&]/, ""));
      params.forEach((value, key) => parsed.searchParams.set(key, value));
    } else {
      parsed.searchParams.set("X-Bogus", signed);
    }
    return parsed.toString();
  }
  if (typeof signed !== "object") return url;

  const result = signed as Record<string, unknown>;
  const signedUrl = pickString(result.url) || pickString(result.signed_url);
  if (signedUrl) return signedUrl;

  const parsed = new URL(url);
  const xBogus = pickString(result["X-Bogus"]) || pickString(result["x-bogus"]);
  const aBogus = pickString(result.a_bogus) || pickString(result["a-bogus"]);
  if (xBogus) parsed.searchParams.set("X-Bogus", xBogus);
  if (aBogus) parsed.searchParams.set("a_bogus", aBogus);
  return parsed.toString();
}

async function harvestSearchViaApi(
  target: Window,
  keyword: string,
  maxItems: number,
): Promise<SearchApiResult> {
  const w = target as unknown as { fetch: FetchLike };
  const items: DouyinSearchItem[] = [];
  const seen = new Set<string>();
  const cap = Math.max(0, Math.floor(maxItems));
  const pageSize = Math.min(20, Math.max(1, cap || 1));
  const paths = [
    "/aweme/v1/web/general/search/single/",
  ];
  let pages = 0;

  for (const path of paths) {
    let offset = 0;
    for (let page = 0; page < 5 && items.length < cap; page += 1) {
      const url = applyDouyinApiSignature(
        target,
        buildSearchApiUrl(target, path, keyword, offset, pageSize),
      );
      let json: unknown;
      try {
        // search/single hangs indefinitely when risk-control withholds the
        // response body (no a_bogus). Abort at 15s — ample for a genuine
        // response — so a stuck path fast-fails instead of pinning the whole
        // keyword task against the 45s content-script bridge timeout.
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 15_000);
        try {
          const resp = await w.fetch(url, {
            credentials: "include",
            signal: controller.signal,
          });
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          json = (await resp.json()) as unknown;
        } finally {
          clearTimeout(timer);
        }
      } catch (err) {
        return { items, pages_fetched: pages,
          error: String(err instanceof Error ? err.message : err) };
      }
      pages += 1;
      const root = json as Record<string, unknown>;
      const statusError = scopeApiStatusError(root);
      if (statusError) return { items, pages_fetched: pages, error: statusError };
      const nilInfo = root.search_nil_info;
      if (nilInfo && typeof nilInfo === "object"
        && (nilInfo as Record<string, unknown>).search_nil_item === "hit_shark") {
        return { items, pages_fetched: pages, error: "hit_shark" };
      }
      for (const item of parseSearchAwemeResponse(json)) {
        const key = item.aweme_id || `${item.title}:${item.author}`;
        if (!key || seen.has(key)) continue;
        seen.add(key);
        items.push(item);
        if (items.length >= cap) break;
      }
      const hasMore = Boolean(root.has_more);
      const nextOffset = Number(root.cursor ?? offset + pageSize);
      if (!hasMore || !Number.isFinite(nextOffset) || nextOffset === offset) break;
      offset = nextOffset;
      await new Promise((r) => setTimeout(r, 300));
    }
    if (items.length > 0) break;
  }

  return { items, pages_fetched: pages };
}

function buildRelatedApiUrl(
  target: Window,
  seedAwemeId: string,
  count: number,
): string {
  const nav = target.navigator;
  const chromeVersion = parseChromeVersion(nav?.userAgent ?? "");
  const platform = nav?.platform || "Win32";
  const isMac = /Mac/i.test(platform);
  const params = new URLSearchParams({
    device_platform: "webapp",
    aid: "6383",
    channel: "channel_pc_web",
    pc_client_type: "1",
    version_code: "290100",
    version_name: "29.1.0",
    cookie_enabled: "true",
    screen_width: String(target.screen?.width ?? 1920),
    screen_height: String(target.screen?.height ?? 1080),
    browser_language: nav?.language || "zh-CN",
    browser_platform: platform,
    browser_name: "Chrome",
    browser_version: chromeVersion,
    browser_online: String(nav?.onLine ?? true),
    engine_name: "Blink",
    engine_version: chromeVersion,
    os_name: isMac ? "Mac OS" : "Windows",
    os_version: isMac ? "10.15.7" : "10",
    platform: "PC",
    msToken: pickCookieValue(target.document?.cookie ?? "", "msToken"),
    aweme_id: seedAwemeId,
    count: String(count),
    filterGids: "",
  });
  return `${target.location.origin}/aweme/v1/web/aweme/related/?${params.toString()}`;
}

function buildFeedApiUrl(target: Window, count: number, refreshIndex: number): string {
  const nav = target.navigator;
  const chromeVersion = parseChromeVersion(nav?.userAgent ?? "");
  const platform = nav?.platform || "Win32";
  const isMac = /Mac/i.test(platform);
  const params = new URLSearchParams({
    device_platform: "webapp",
    aid: "6383",
    channel: "channel_pc_web",
    pc_client_type: "1",
    version_code: "290100",
    version_name: "29.1.0",
    cookie_enabled: "true",
    screen_width: String(target.screen?.width ?? 1920),
    screen_height: String(target.screen?.height ?? 1080),
    browser_language: nav?.language || "zh-CN",
    browser_platform: platform,
    browser_name: "Chrome",
    browser_version: chromeVersion,
    browser_online: String(nav?.onLine ?? true),
    engine_name: "Blink",
    engine_version: chromeVersion,
    os_name: isMac ? "Mac OS" : "Windows",
    os_version: isMac ? "10.15.7" : "10",
    platform: "PC",
    msToken: pickCookieValue(target.document?.cookie ?? "", "msToken"),
    count: String(count),
    tag_id: "",
    share_aweme_id: "",
    live_insert_type: "",
    refresh_index: String(refreshIndex),
    video_type_select: "1",
    aweme_pc_rec_raw_data: '{"is_client":"false"}',
    globalwid: "",
    pull_type: "",
    min_window: "",
    free_right: "",
    ug_source: "",
    creative_id: "",
  });
  return `${target.location.origin}/aweme/v1/web/tab/feed/?${params.toString()}`;
}

async function harvestHotRelatedViaApi(
  target: Window,
  seedAwemeId: string,
  maxItems: number,
  meta: HotRelatedMeta,
): Promise<SearchApiResult> {
  const w = target as unknown as { fetch: FetchLike };
  const cap = Math.max(0, Math.floor(maxItems));
  if (!seedAwemeId || cap <= 0) return { items: [], pages_fetched: 0 };
  const url = applyDouyinApiSignature(
    target,
    buildRelatedApiUrl(target, seedAwemeId, Math.min(20, cap)),
  );
  const resp = await w.fetch(url, { credentials: "include" });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = (await resp.json()) as unknown;
  return {
    items: parseRelatedAwemeResponse(json, meta).slice(0, cap),
    pages_fetched: 1,
  };
}

async function harvestFeedViaApi(target: Window, maxItems: number): Promise<SearchApiResult> {
  const w = target as unknown as { fetch: FetchLike };
  const cap = Math.max(0, Math.floor(maxItems));
  if (cap <= 0) return { items: [], pages_fetched: 0 };
  const requestCount = Math.min(20, Math.max(10, cap * 2));
  const url = applyDouyinApiSignature(target, buildFeedApiUrl(target, requestCount, 1));
  const resp = await w.fetch(url, { credentials: "include" });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const json = (await resp.json()) as unknown;
  return {
    items: parseFeedAwemeResponse(json).slice(0, cap),
    pages_fetched: 1,
  };
}

/**
 * Filter bridge traffic to this page's Window and origin.
 *
 * This only reduces accidental cross-frame/page message handling. It is not
 * an authorization boundary because any same-page script can post a matching
 * message; request shape and correlation checks remain mandatory below.
 */
export function isSameWindowSameOriginApiRequest(
  event: MessageEvent,
  target: Window,
): boolean {
  return event.source === target && event.origin === target.location.origin;
}

const API_REQUEST_DEDUP_TIMEOUT_MS = 120_000;
const API_REQUEST_MAX_IN_FLIGHT = 128;

type PostApiResponse = (message: Record<string, unknown>) => void;

function startApiRequestOnce(
  target: Window,
  state: ApiHarvesterInstallState,
  requestType: string,
  requestId: string,
  run: (postResponse: PostApiResponse) => Promise<void>,
): void {
  const key = `${requestType}:${requestId}`;
  if (state.activeRequests.has(key)) return;

  // A page should have only a handful of live bridge requests. Keep the
  // registry bounded even if page fetches hang forever and timers are heavily
  // throttled in a background tab.
  if (state.activeRequests.size >= API_REQUEST_MAX_IN_FLIGHT) {
    const oldestKey = state.activeRequests.keys().next().value as string | undefined;
    if (oldestKey !== undefined) {
      const oldest = state.activeRequests.get(oldestKey);
      if (oldest) clearTimeout(oldest.timeoutId);
      state.activeRequests.delete(oldestKey);
    }
  }

  const token = {};
  const timeoutId = setTimeout(() => {
    const current = state.activeRequests.get(key);
    if (current?.token === token) state.activeRequests.delete(key);
  }, state.requestTimeoutMs);
  const entry: ActiveApiRequest = { token, timeoutId };
  state.activeRequests.set(key, entry);

  const postResponse: PostApiResponse = (message) => {
    // Suppress results from requests that outlived the bridge timeout (or were
    // evicted by the safety bound). Their isolated-world listener is gone.
    if (state.activeRequests.get(key)?.token !== token) return;
    target.postMessage(message, target.location.origin);
  };
  const cleanup = (): void => {
    clearTimeout(timeoutId);
    if (state.activeRequests.get(key)?.token === token) {
      state.activeRequests.delete(key);
    }
  };

  // Both fulfillment and rejection release the request id. Individual
  // handlers below translate expected harvest errors into bridge responses.
  void run(postResponse).then(cleanup, cleanup);
}

export function installApiHarvester(
  target: Window,
  requestTimeoutMs: number = API_REQUEST_DEDUP_TIMEOUT_MS,
): void {
  const shared = getDouyinMainBridgeState(target);
  if (shared.apiHarvester) return;

  const state: ApiHarvesterInstallState = {
    listener: (): void => {},
    activeRequests: new Map(),
    requestTimeoutMs: Math.max(1, Math.floor(requestTimeoutMs)),
  };
  const listener: EventListener = (rawEvent) => {
    const event = rawEvent as MessageEvent;
    if (!isSameWindowSameOriginApiRequest(event, target)) return;
    const data = (event?.data ?? null) as Record<string, unknown> | null;
    if (!data || typeof data !== "object") return;
    if (data.type === SEARCH_API_REQUEST_TYPE) {
      const requestId = String(data.requestId ?? "");
      const keyword = String(data.keyword ?? "").trim();
      const maxItems = Number(data.maxItems ?? 0);
      if (!requestId || !keyword) return;
      startApiRequestOnce(target, state, SEARCH_API_REQUEST_TYPE, requestId, async (post) => {
        try {
          const result = await harvestSearchViaApi(target, keyword, maxItems);
          post({
            type: SEARCH_API_RESPONSE_TYPE,
            requestId,
            items: result.items,
            pages_fetched: result.pages_fetched,
            ...(result.error ? { error: result.error } : {}),
          });
        } catch (err) {
          post({
            type: SEARCH_API_RESPONSE_TYPE,
            requestId,
            items: [],
            pages_fetched: 0,
            error: String(err instanceof Error ? err.message : err),
          });
        }
      });
      return;
    }
    if (data.type === HOT_API_REQUEST_TYPE) {
      const requestId = String(data.requestId ?? "");
      const seedAwemeId = String(data.seedAwemeId ?? "").trim();
      const maxItems = Number(data.maxItems ?? 0);
      const word = String(data.word ?? "");
      const sentenceId = String(data.sentenceId ?? "");
      if (!requestId || !seedAwemeId) return;
      startApiRequestOnce(target, state, HOT_API_REQUEST_TYPE, requestId, async (post) => {
        try {
          const result = await harvestHotRelatedViaApi(target, seedAwemeId, maxItems, {
            word,
            sentenceId,
            seedAwemeId,
          });
          post({
            type: HOT_API_RESPONSE_TYPE,
            requestId,
            items: result.items,
            pages_fetched: result.pages_fetched,
          });
        } catch (err) {
          post({
            type: HOT_API_RESPONSE_TYPE,
            requestId,
            items: [],
            pages_fetched: 0,
            error: String(err instanceof Error ? err.message : err),
          });
        }
      });
      return;
    }
    if (data.type === FEED_API_REQUEST_TYPE) {
      const requestId = String(data.requestId ?? "");
      const maxItems = Number(data.maxItems ?? 0);
      if (!requestId) return;
      startApiRequestOnce(target, state, FEED_API_REQUEST_TYPE, requestId, async (post) => {
        try {
          const result = await harvestFeedViaApi(target, maxItems);
          post({
            type: FEED_API_RESPONSE_TYPE,
            requestId,
            items: result.items,
            pages_fetched: result.pages_fetched,
          });
        } catch (err) {
          post({
            type: FEED_API_RESPONSE_TYPE,
            requestId,
            items: [],
            pages_fetched: 0,
            error: String(err instanceof Error ? err.message : err),
          });
        }
      });
      return;
    }
    if (data.type === IDENTITY_REQUEST_TYPE) {
      const requestId = String(data.requestId ?? "");
      if (!requestId) return;
      startApiRequestOnce(target, state, IDENTITY_REQUEST_TYPE, requestId, async (post) => {
        try {
          const secUid = await resolveDouyinSelfSecUid(target);
          post({
            type: IDENTITY_RESPONSE_TYPE,
            requestId,
            secUid,
            ...(!secUid ? { error: "identity_unavailable" } : {}),
          });
        } catch (err) {
          post({
            type: IDENTITY_RESPONSE_TYPE,
            requestId,
            secUid: "",
            error: String(err instanceof Error ? err.message : err),
          });
        }
      });
      return;
    }
    if (data.type !== API_REQUEST_TYPE) return;
    const requestId = String(data.requestId ?? "");
    const scope = data.scope as DouyinScope;
    const secUid = String(data.secUid ?? "");
    const maxItems = Number(data.maxItems ?? 0);
    if (!requestId || !scope || !secUid) return;
    startApiRequestOnce(target, state, API_REQUEST_TYPE, requestId, async (post) => {
      try {
        const result = await harvestScopeViaApi(target, scope, secUid, maxItems);
        post({
          type: API_RESPONSE_TYPE,
          requestId,
          items: result.items,
          pages_fetched: result.pages_fetched,
          ...(result.error ? { error: result.error } : {}),
        });
      } catch (err) {
        post({
          type: API_RESPONSE_TYPE,
          requestId,
          items: [],
          pages_fetched: 0,
          error: String(err instanceof Error ? err.message : err),
        });
      }
    });
  };

  state.listener = listener;
  shared.apiHarvester = state;
  target.addEventListener("message", listener);
}

/**
 * Ensure every MAIN-world bridge hook still wraps the page's live primitives.
 *
 * Douyin may replace window.fetch or XMLHttpRequest.prototype.open after its
 * SDK initializes or during an SPA transition. The individual installers are
 * Window-idempotent but deliberately re-wrap when the page replaced a hook, so
 * every explicit bundle reinjection must call them again instead of treating
 * an old "installed" marker as proof that the live wrapper still exists.
 */
export function installDouyinMainBridge(target: Window): void {
  const postItems = (items: DouyinBootstrapItem[], scope: DouyinScope): void => {
    target.postMessage(
      { type: FETCH_TAP_MESSAGE_TYPE, scope, items },
      target.location.origin,
    );
  };
  const postSearchItems = (
    items: DouyinSearchItem[],
    scope: DouyinSearchScope,
  ): void => {
    target.postMessage(
      { type: SEARCH_TAP_MESSAGE_TYPE, scope, items, response_observed: true },
      target.location.origin,
    );
  };
  installFetchTap(target, postItems, postSearchItems);
  installXhrTap(target, postItems, postSearchItems);
  installApiHarvester(target);
}

export function autoInstallDouyinMainBridge(
  target: Window,
  waitForSdk: (target: Window, timeoutMs: number) => Promise<boolean> = waitForDouyinSdk,
): void {
  const shared = getDouyinMainBridgeState(target);
  if (shared.autoInstallStatus === "installed") {
    // Re-validate the live primitives before replaying the sentinel. The page
    // may have replaced fetch/XHR since the earlier bundle evaluation.
    installDouyinMainBridge(target);
    replayInstallStatusPing(target, "installed");
    return;
  }

  // A dynamic reinjection can arrive while the first SDK readiness wait is
  // still pending. Revalidate live fetch/XHR on every evaluation; only the
  // readiness waiter itself remains single-flight.
  installDouyinMainBridge(target);
  if (shared.autoInstallStatus === "pending") return;

  shared.autoInstallStatus = "pending";
  // Generous timeout: real e2e probe (2026-05-08) showed skipped_no_sdk
  // on slow page-bundle loads even when the user was logged in.
  void waitForSdk(target, 15_000).then((ready) => {
    if (!ready) {
      // Allow a later explicit injection to retry after a slow SDK load.
      delete shared.autoInstallStatus;
      replayInstallStatusPing(target, "skipped_no_sdk");
      // eslint-disable-next-line no-console
      console.debug("[OpenBiliClaw] dy fetch-tap skipped: SDK not detected");
      return;
    }
    installDouyinMainBridge(target);
    shared.autoInstallStatus = "installed";
    replayInstallStatusPing(target, "installed");
    // eslint-disable-next-line no-console
    console.debug("[OpenBiliClaw] dy fetch-tap + API harvester installed (MAIN world)");
  });
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  autoInstallDouyinMainBridge(window);
}
