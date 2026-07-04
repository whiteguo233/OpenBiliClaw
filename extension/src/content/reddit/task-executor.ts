/**
 * Reddit content-script executor for extension-backed discovery tasks.
 *
 * Runs inside reddit.com with the user's current browser session. Requests use
 * same-origin JSON endpoints with credentials included; cookies never leave the
 * browser except through normalized, read-only result rows.
 */

export type RedditDiscoveryTaskType = "search" | "hot" | "subreddit" | "related";
export type RedditTaskType = RedditDiscoveryTaskType | "bootstrap_events";
export type RedditScope =
  | "reddit_search"
  | "reddit_hot"
  | "reddit_subreddit"
  | "reddit_related"
  | "reddit_saved"
  | "reddit_upvoted"
  | "reddit_subscribed";

export interface RedditTaskItem {
  scope: RedditScope;
  content_type: "post" | "comment" | "subreddit";
  id: string;
  name: string;
  title: string;
  url: string;
  permalink?: string;
  outbound_url?: string;
  subreddit?: string;
  author?: string;
  score?: number;
  num_comments?: number;
  selftext?: string;
  body?: string;
  public_description?: string;
  search_keyword?: string;
  source_strategy?: string;
  source_keyword_id?: number;
}

export interface RedditExecuteMessage {
  task_id: string;
  type?: RedditTaskType;
  keywords?: string[];
  max_items_per_keyword?: number;
  source_keyword_ids?: Record<string, number>;
  subreddit?: string;
  subreddits?: string[];
  max_items?: number;
  max_items_per_subreddit?: number;
  related_urls?: string[];
  max_items_per_seed?: number;
  max_items_per_scope?: number;
  fetch_timeout_ms?: number;
}

export interface RedditTaskResult {
  task_id: string;
  status: "ok" | "empty" | "failed";
  items: RedditTaskItem[];
  scope_counts: Record<string, number>;
  error?: string;
  debug?: Record<string, unknown>;
}

export interface RedditNormalizeContext {
  scope: RedditScope;
  strategy: string;
  searchKeyword?: string;
  sourceKeywordId?: number;
}

class RedditHttpError extends Error {
  readonly status: number;
  readonly body: string;
  readonly requestUrl: string;

  constructor(status: number, body: string, requestUrl: string) {
    super(`HTTP ${status}`);
    this.name = "RedditHttpError";
    this.status = status;
    this.body = body;
    this.requestUrl = requestUrl;
  }
}

const REDDIT_FETCH_TIMEOUT_MS = 30_000;
const REDDIT_TASK_LISTENER_SENTINEL = "__OPENBILICLAW_REDDIT_TASK_LISTENER__";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function str(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value).trim() : "";
}

function num(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return undefined;
}

function fetchTimeoutMs(message: RedditExecuteMessage): number {
  const value = Number(message.fetch_timeout_ms);
  return Number.isFinite(value) && value > 0 ? value : REDDIT_FETCH_TIMEOUT_MS;
}

function absoluteRedditUrl(value: string): string {
  if (!value) return "";
  if (value.startsWith("//")) return `https:${value}`;
  if (value.startsWith("/")) return `https://www.reddit.com${value}`;
  return value;
}

function normalizeSubreddit(value: string): string {
  return value.trim().replace(/^r\//i, "") || "all";
}

function postIdFromRedditUrl(value: string): string {
  const match = value.match(/(?:\/comments\/|redd\.it\/)([A-Za-z0-9_]+)/);
  return match?.[1] ?? "";
}

export function normalizeRedditListingChild(
  raw: unknown,
  context: RedditNormalizeContext,
): RedditTaskItem | null {
  const child = asRecord(raw);
  const data = asRecord(child.data ?? raw);
  const kind = str(child.kind) || str(data.kind) || str(data.name).slice(0, 2);
  const id = str(data.id) || str(data.post_id) || postIdFromRedditUrl(str(data.permalink));
  const name = str(data.name) || (id ? `${kind === "t1" ? "t1" : "t3"}_${id}` : "");
  const contentType: "post" | "comment" =
    kind === "t1" || name.startsWith("t1_") || (!!str(data.body) && !str(data.title))
      ? "comment"
      : "post";
  const permalink = str(data.permalink);
  const url = absoluteRedditUrl(permalink || str(data.url));
  const title =
    str(data.title) ||
    str(data.link_title) ||
    str(data.body).slice(0, 80) ||
    str(data.selftext).slice(0, 80);
  if (!id && !url) return null;
  if (!title && !url) return null;

  const item: RedditTaskItem = {
    scope: context.scope,
    content_type: contentType,
    id,
    name,
    title,
    url,
    source_strategy: context.strategy,
  };
  if (permalink) item.permalink = permalink;
  const outboundUrl = str(data.url);
  if (outboundUrl && absoluteRedditUrl(outboundUrl) !== url) {
    item.outbound_url = absoluteRedditUrl(outboundUrl);
  }
  const subreddit = str(data.subreddit) || str(data.subreddit_name_prefixed).replace(/^r\//i, "");
  if (subreddit) item.subreddit = subreddit;
  const author = str(data.author);
  if (author && author !== "[deleted]") item.author = author;
  const score = num(data.score ?? data.ups);
  if (score !== undefined) item.score = score;
  const comments = num(data.num_comments);
  if (comments !== undefined) item.num_comments = comments;
  const selftext = str(data.selftext);
  if (selftext) item.selftext = selftext;
  const body = str(data.body);
  if (body) item.body = body;
  if (context.searchKeyword) item.search_keyword = context.searchKeyword;
  if (context.sourceKeywordId !== undefined) item.source_keyword_id = context.sourceKeywordId;
  return item;
}

export function collectRedditListingItems(
  raw: unknown,
  context: RedditNormalizeContext,
): RedditTaskItem[] {
  if (Array.isArray(raw)) {
    return raw.flatMap((item) => collectRedditListingItems(item, context));
  }
  const row = asRecord(raw);
  const data = asRecord(row.data);
  const children = data.children;
  if (Array.isArray(children)) {
    return children
      .map((child) => normalizeRedditListingChild(child, context))
      .filter((item): item is RedditTaskItem => item !== null);
  }
  const item = normalizeRedditListingChild(raw, context);
  return item ? [item] : [];
}

export function buildRedditJsonUrl(
  type: RedditDiscoveryTaskType,
  input: string,
  limit: number,
): string {
  const capped = Math.max(1, Math.floor(Number(limit) || 1));
  if (type === "search") {
    const params = new URLSearchParams({ q: input, limit: String(capped), sort: "relevance" });
    return `https://www.reddit.com/search.json?${params.toString()}`;
  }
  if (type === "hot") {
    return `https://www.reddit.com/r/${encodeURIComponent(normalizeSubreddit(input))}/hot.json?limit=${capped}`;
  }
  if (type === "subreddit") {
    return `https://www.reddit.com/r/${encodeURIComponent(normalizeSubreddit(input))}.json?limit=${capped}`;
  }

  let target = input.trim();
  const postId = postIdFromRedditUrl(target);
  if (!target.startsWith("http") && postId) {
    target = `https://www.reddit.com/comments/${postId}/`;
  }
  if (target.includes("redd.it/") && postId) {
    target = `https://www.reddit.com/comments/${postId}/`;
  }
  const url = new URL(target);
  url.search = "";
  url.hash = "";
  const base = url.toString().replace(/\/?$/, "/");
  return `${base}.json?limit=${capped}`;
}

function buildRedditUserJsonUrl(
  username: string,
  feed: "saved" | "upvoted",
  limit: number,
): string {
  const capped = Math.max(1, Math.floor(Number(limit) || 1));
  return `https://www.reddit.com/user/${encodeURIComponent(username)}/${feed}.json?limit=${capped}`;
}

function buildRedditSubscribedJsonUrl(limit: number): string {
  const capped = Math.max(1, Math.floor(Number(limit) || 1));
  return `https://www.reddit.com/subreddits/mine/subscriber.json?limit=${capped}`;
}

async function fetchCurrentRedditUsername(timeoutMs: number): Promise<string> {
  const raw = await fetchRedditJson("https://www.reddit.com/api/me.json", timeoutMs);
  const row = asRecord(raw);
  const data = asRecord(row.data);
  const username = str(data.name) || str(row.name) || str(data.username);
  if (!username) {
    throw new RedditHttpError(401, "reddit login required", "https://www.reddit.com/api/me.json");
  }
  return username;
}

function normalizeRedditSubredditChild(raw: unknown): RedditTaskItem | null {
  const child = asRecord(raw);
  const data = asRecord(child.data ?? raw);
  const displayName = str(data.display_name) || str(data.display_name_prefixed).replace(/^r\//i, "");
  const title = str(data.title) || (displayName ? `r/${displayName}` : "");
  if (!displayName && !title) return null;
  const url = absoluteRedditUrl(str(data.url) || (displayName ? `/r/${displayName}/` : ""));
  const item: RedditTaskItem = {
    scope: "reddit_subscribed",
    content_type: "subreddit",
    id: displayName,
    name: str(data.name) || (displayName ? `sr_${displayName}` : ""),
    title: title.startsWith("r/") ? title : `r/${title}`,
    url,
    subreddit: displayName,
    source_strategy: "reddit-bootstrap-subscribed",
  };
  const publicDescription = str(data.public_description) || str(data.description);
  if (publicDescription) item.public_description = publicDescription;
  return item;
}

function collectRedditSubscribedItems(raw: unknown): RedditTaskItem[] {
  if (Array.isArray(raw)) {
    return raw.flatMap((item) => collectRedditSubscribedItems(item));
  }
  const row = asRecord(raw);
  const data = asRecord(row.data);
  const children = data.children;
  if (Array.isArray(children)) {
    return children
      .map((child) => normalizeRedditSubredditChild(child))
      .filter((item): item is RedditTaskItem => item !== null);
  }
  const item = normalizeRedditSubredditChild(raw);
  return item ? [item] : [];
}

async function fetchRedditJson(
  url: string,
  timeoutMs: number = REDDIT_FETCH_TIMEOUT_MS,
): Promise<unknown> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), Math.max(1, timeoutMs));
  try {
    const resp = await fetch(url, {
      method: "GET",
      credentials: "include",
      headers: {
        accept: "application/json,text/plain,*/*",
      },
      signal: controller.signal,
    });
    const text = await resp.text();
    if (!resp.ok) {
      throw new RedditHttpError(resp.status, text.slice(0, 500), url);
    }
    try {
      return JSON.parse(text);
    } catch {
      throw new RedditHttpError(resp.status, text.slice(0, 500), url);
    }
  } finally {
    clearTimeout(timeoutId);
  }
}

function isLoginRequiredError(error: unknown): boolean {
  if (!(error instanceof RedditHttpError)) return false;
  if (error.status === 401 || error.status === 403) return true;
  return error.body.toLowerCase().includes("login");
}

function scopeForTask(type: RedditTaskType): RedditScope {
  if (type === "hot") return "reddit_hot";
  if (type === "subreddit") return "reddit_subreddit";
  if (type === "related") return "reddit_related";
  if (type === "bootstrap_events") return "reddit_saved";
  return "reddit_search";
}

function strategyForTask(type: RedditTaskType): string {
  return `reddit-${type}`;
}

function incrementScopeCounts(items: RedditTaskItem[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const item of items) {
    counts[item.scope] = (counts[item.scope] ?? 0) + 1;
  }
  return counts;
}

async function runBootstrapFetches(message: RedditExecuteMessage): Promise<RedditTaskItem[]> {
  const limit = Math.max(1, Number(message.max_items_per_scope) || Number(message.max_items) || 300);
  const timeoutMs = fetchTimeoutMs(message);
  const username = await fetchCurrentRedditUsername(timeoutMs);
  const rows: RedditTaskItem[] = [];
  let firstError: unknown = null;

  const fetches: Array<{
    scope: "reddit_saved" | "reddit_upvoted";
    strategy: string;
    url: string;
  }> = [
    {
      scope: "reddit_saved",
      strategy: "reddit-bootstrap-saved",
      url: buildRedditUserJsonUrl(username, "saved", limit),
    },
    {
      scope: "reddit_upvoted",
      strategy: "reddit-bootstrap-upvoted",
      url: buildRedditUserJsonUrl(username, "upvoted", limit),
    },
  ];

  for (const fetchSpec of fetches) {
    try {
      const raw = await fetchRedditJson(fetchSpec.url, timeoutMs);
      rows.push(
        ...collectRedditListingItems(raw, {
          scope: fetchSpec.scope,
          strategy: fetchSpec.strategy,
        }),
      );
    } catch (error) {
      firstError ??= error;
    }
  }

  try {
    const raw = await fetchRedditJson(buildRedditSubscribedJsonUrl(limit), timeoutMs);
    rows.push(...collectRedditSubscribedItems(raw));
  } catch (error) {
    firstError ??= error;
  }

  if (rows.length === 0 && firstError) {
    throw firstError;
  }
  return rows;
}

async function runTaskFetches(message: RedditExecuteMessage): Promise<RedditTaskItem[]> {
  const type = message.type ?? "search";
  const timeoutMs = fetchTimeoutMs(message);
  if (type === "bootstrap_events") {
    return runBootstrapFetches(message);
  }
  const scope = scopeForTask(type);
  const strategy = strategyForTask(type);
  const rows: RedditTaskItem[] = [];

  if (type === "search") {
    const keywords = Array.isArray(message.keywords) ? message.keywords : [];
    const keywordIds = isRecord(message.source_keyword_ids) ? message.source_keyword_ids : {};
    const limit = Math.max(1, Number(message.max_items_per_keyword) || Number(message.max_items) || 10);
    for (const keyword of keywords) {
      const value = str(keyword);
      if (!value) continue;
      const raw = await fetchRedditJson(buildRedditJsonUrl("search", value, limit), timeoutMs);
      rows.push(
        ...collectRedditListingItems(raw, {
          scope,
          strategy,
          searchKeyword: value,
          sourceKeywordId: num(keywordIds[value]),
        }),
      );
    }
    return rows;
  }

  if (type === "hot") {
    const subreddit = str(message.subreddit) || "all";
    const limit = Math.max(1, Number(message.max_items) || 10);
    const raw = await fetchRedditJson(buildRedditJsonUrl("hot", subreddit, limit), timeoutMs);
    return collectRedditListingItems(raw, { scope, strategy });
  }

  if (type === "subreddit") {
    const subreddits =
      Array.isArray(message.subreddits) && message.subreddits.length > 0
        ? message.subreddits
        : [str(message.subreddit) || "all"];
    const limit = Math.max(1, Number(message.max_items_per_subreddit) || Number(message.max_items) || 10);
    for (const subreddit of subreddits) {
      const value = str(subreddit);
      if (!value) continue;
      const raw = await fetchRedditJson(buildRedditJsonUrl("subreddit", value, limit), timeoutMs);
      rows.push(...collectRedditListingItems(raw, { scope, strategy }));
    }
    return rows;
  }

  const urls = Array.isArray(message.related_urls) ? message.related_urls : [];
  const limit = Math.max(1, Number(message.max_items_per_seed) || 10);
  for (const url of urls) {
    const value = str(url);
    if (!value) continue;
    const raw = await fetchRedditJson(buildRedditJsonUrl("related", value, limit), timeoutMs);
    rows.push(...collectRedditListingItems(raw, { scope, strategy }));
  }
  return rows;
}

export async function executeRedditTask(message: RedditExecuteMessage): Promise<RedditTaskResult> {
  const taskId = str(message.task_id);
  if (!taskId) {
    return {
      task_id: "",
      status: "failed",
      items: [],
      scope_counts: {},
      error: "task_id_required",
    };
  }
  try {
    const items = await runTaskFetches(message);
    return {
      task_id: taskId,
      status: items.length > 0 ? "ok" : "empty",
      items,
      scope_counts: incrementScopeCounts(items),
    };
  } catch (error) {
    return {
      task_id: taskId,
      status: "failed",
      items: [],
      scope_counts: {},
      error: isLoginRequiredError(error) ? "reddit_login_required" : "reddit_task_failed",
      debug: {
        message: error instanceof Error ? error.message : String(error),
      },
    };
  }
}

export function installRedditMessageListener(): void {
  if (typeof chrome === "undefined" || !chrome.runtime?.onMessage) return;
  const g = globalThis as unknown as Record<string, unknown>;
  if (g[REDDIT_TASK_LISTENER_SENTINEL]) return;
  g[REDDIT_TASK_LISTENER_SENTINEL] = true;
  chrome.runtime.onMessage.addListener(
    (message: unknown, _sender, sendResponse: (response: unknown) => void) => {
      const payload = asRecord(message);
      if (payload.action !== "REDDIT_TASK_EXECUTE") return false;
      void executeRedditTask(asRecord(payload.data) as unknown as RedditExecuteMessage)
        .then((result) => {
          return chrome.runtime.sendMessage({ action: "REDDIT_TASK_RESULT", data: result });
        })
        .then(() => {
          sendResponse({ ok: true });
        })
        .catch((error: unknown) => {
          sendResponse({
            ok: false,
            error: error instanceof Error ? error.message : String(error),
          });
        });
      return true;
    },
  );
}
