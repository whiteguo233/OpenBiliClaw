/**
 * Zhihu content-script executor for fetch-only bootstrap_events tasks.
 *
 * Runs inside zhihu.com with the user's active browser session, so requests use
 * `credentials: "include"` without exporting cookies to the backend.
 */

export type ZhihuScope =
  | "zhihu_read_history"
  | "zhihu_activity"
  | "zhihu_collection"
  | "zhihu_search"
  | "zhihu_hot"
  | "zhihu_feed"
  | "zhihu_creator"
  | "zhihu_related";
export type ZhihuTaskType = "bootstrap_events" | "search" | "hot" | "feed" | "creator" | "related";

export interface ZhihuBootstrapItem {
  scope: ZhihuScope;
  content_type: string;
  content_id: string;
  title: string;
  author: string;
  author_url?: string;
  url: string;
  question_id?: string;
  summary?: string;
  interaction_action?: string;
  interaction_time?: string;
  voteup?: number;
  favorite_count?: number;
  comment_count?: number;
  collection_id?: string;
  collection_name?: string;
  search_keyword?: string;
  source_strategy?: string;
  source_keyword_id?: number;
}

export interface ZhihuExecuteMessage {
  task_id: string;
  type?: ZhihuTaskType;
  scopes?: ZhihuScope[];
  profile_slug?: string;
  max_items_per_scope?: number;
  max_collections?: number;
  keywords?: string[];
  max_items_per_keyword?: number;
  source_keyword_ids?: Record<string, number>;
  max_items?: number;
  creator_urls?: string[];
  max_items_per_creator?: number;
  related_urls?: string[];
  max_items_per_seed?: number;
}

export interface ZhihuTaskResult {
  task_id: string;
  status: "ok" | "empty" | "failed";
  items: ZhihuBootstrapItem[];
  scope_counts: Record<string, number>;
  error?: string;
  debug?: Record<string, unknown>;
}

interface ZhihuCollectionMeta {
  id: string;
  name: string;
}

interface ZhihuMemberMeta {
  urlToken: string;
}

interface ZhihuCollectionFetchResult {
  items: ZhihuBootstrapItem[];
  debug: Record<string, unknown>;
}

const DEFAULT_SCOPES: readonly ZhihuScope[] = [
  "zhihu_read_history",
  "zhihu_collection",
];
const ZHIHU_FETCH_TIMEOUT_MS = 30_000;
const MAX_SAFE_INTEGER_TEXT = String(Number.MAX_SAFE_INTEGER);

class ZhihuHttpError extends Error {
  readonly status: number;
  readonly body: string;
  readonly requestUrl: string;

  constructor(status: number, body: string, requestUrl: string) {
    super(`HTTP ${status}`);
    this.name = "ZhihuHttpError";
    this.status = status;
    this.body = body;
    this.requestUrl = requestUrl;
  }
}

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

function isUnsafeJsonIntegerDigits(digits: string): boolean {
  const normalized = digits.replace(/^0+/, "") || "0";
  if (normalized.length > MAX_SAFE_INTEGER_TEXT.length) return true;
  return normalized.length === MAX_SAFE_INTEGER_TEXT.length && normalized > MAX_SAFE_INTEGER_TEXT;
}

function quoteUnsafeJsonIntegers(text: string): string {
  let out = "";
  let i = 0;
  while (i < text.length) {
    const ch = text[i]!;
    if (ch === '"') {
      const start = i;
      i++;
      while (i < text.length) {
        if (text[i] === "\\") {
          i += 2;
          continue;
        }
        if (text[i] === '"') {
          i++;
          break;
        }
        i++;
      }
      out += text.slice(start, i);
      continue;
    }

    if (ch === "-" || (ch >= "0" && ch <= "9")) {
      const start = i;
      if (text[i] === "-") i++;
      const digitStart = i;
      while (i < text.length && text[i]! >= "0" && text[i]! <= "9") i++;
      const digits = text.slice(digitStart, i);
      if (digits && text[i] !== "." && text[i] !== "e" && text[i] !== "E") {
        const token = text.slice(start, i);
        out += isUnsafeJsonIntegerDigits(digits) ? `"${token}"` : token;
        continue;
      }
      while (i < text.length && /[0-9eE+\-.]/.test(text[i]!)) i++;
      out += text.slice(start, i);
      continue;
    }

    out += ch;
    i++;
  }
  return out;
}

function parseJsonPreservingLargeIntegers(text: string): unknown {
  return JSON.parse(quoteUnsafeJsonIntegers(text));
}

function absoluteZhihuUrl(url: string): string {
  if (!url) return "";
  if (url.startsWith("//")) return `https:${url}`;
  if (url.startsWith("/")) return `https://www.zhihu.com${url}`;
  return url;
}

function answerUrlParts(url: string): { questionId: string; answerId: string } | null {
  const match = url.match(/\/question\/(\d+)\/answer\/(\d+)(?:[/?#]|$)/);
  if (!match?.[1] || !match[2]) return null;
  return { questionId: match[1], answerId: match[2] };
}

function questionIdFromUrl(url: string): string {
  const match = url.match(/\/question\/(\d+)(?:[/?#]|$)/);
  return match?.[1] ?? "";
}

function articleIdFromUrl(url: string): string {
  const match = url.match(/(?:zhuanlan\.zhihu\.com\/p\/|\/p\/)(\d+)(?:[/?#]|$)/);
  return match?.[1] ?? "";
}

function contentIdFromUrl(contentType: string, url: string): string {
  if (contentType === "answer") return answerUrlParts(url)?.answerId ?? "";
  if (contentType === "article") return articleIdFromUrl(url);
  if (contentType === "question") return questionIdFromUrl(url);
  return "";
}

function currentUrl(): string {
  try {
    return globalThis.location?.href ?? "";
  } catch {
    return "";
  }
}

function isLoginRequiredError(error: unknown): error is ZhihuHttpError {
  if (!(error instanceof ZhihuHttpError)) return false;
  const href = currentUrl();
  if (href.includes("/signin")) return true;
  if (error.status === 401) return true;
  if (error.status === 403) {
    return !error.body.includes("10003") && !error.body.includes("请求参数异常");
  }
  return error.status === 400 && error.body.includes("BadRequestError");
}

function answerUrl(questionId: string, answerId: string, fallback = ""): string {
  const fallbackParts = answerUrlParts(fallback);
  if (fallbackParts) {
    return `https://www.zhihu.com/question/${fallbackParts.questionId}/answer/${fallbackParts.answerId}`;
  }
  return questionId && answerId
    ? `https://www.zhihu.com/question/${questionId}/answer/${answerId}`
    : "";
}

function articleUrl(articleId: string, fallback = ""): string {
  const fallbackArticleId = articleIdFromUrl(fallback);
  if (fallbackArticleId) return `https://zhuanlan.zhihu.com/p/${fallbackArticleId}`;
  return articleId ? `https://zhuanlan.zhihu.com/p/${articleId}` : fallback;
}

function questionUrl(questionId: string, fallback = ""): string {
  const fallbackQuestionId = questionIdFromUrl(fallback);
  if (fallbackQuestionId) return `https://www.zhihu.com/question/${fallbackQuestionId}`;
  return questionId ? `https://www.zhihu.com/question/${questionId}` : fallback;
}

function stripHtml(value: string): string {
  return value
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .trim();
}

function extractPeopleSlug(url: string): string {
  const match = url.match(/\/people\/([^/?#]+)/);
  return match?.[1] ? decodeURIComponent(match[1]) : "";
}

function peopleUrlFromAuthor(author: Record<string, unknown>, fallback = ""): string {
  const token = str(author.url_token) || str(author.urlToken) || extractPeopleSlug(str(author.url));
  if (token) return `https://www.zhihu.com/people/${encodeURIComponent(token)}`;
  return absoluteZhihuUrl(str(author.url) || fallback);
}

function normalizeCurrentMember(raw: unknown): ZhihuMemberMeta | null {
  const row = asRecord(raw);
  const urlToken =
    str(row.url_token) ||
    str(row.urlToken) ||
    extractPeopleSlug(str(row.url)) ||
    extractPeopleSlug(str(row.url_template));
  return urlToken ? { urlToken } : null;
}

async function fetchCurrentMember(): Promise<ZhihuMemberMeta | null> {
  return normalizeCurrentMember(await fetchJson("/api/v4/me"));
}

export function normalizeZhihuReadHistory(raw: unknown): ZhihuBootstrapItem | null {
  const row = asRecord(raw);
  const data = asRecord(row.data);
  const header = asRecord(data.header);
  const content = asRecord(data.content);
  const action = asRecord(data.action);
  const extra = asRecord(data.extra);

  const contentType = str(extra.content_type);
  const contentId = str(extra.content_token);
  const questionId = str(extra.question_token);
  const title = str(header.title) || str(content.title) || str(content.summary) || contentId;
  const url = absoluteZhihuUrl(str(action.url));
  if (!contentType || !contentId || (!title && !url)) return null;

  const item: ZhihuBootstrapItem = {
    scope: "zhihu_read_history",
    content_type: contentType,
    content_id: contentId,
    title,
    author: str(content.author_name),
    url,
  };
  if (questionId) item.question_id = questionId;
  const summary = str(content.summary);
  if (summary) item.summary = summary;
  const readTime = str(extra.read_time);
  if (readTime) item.interaction_time = readTime;
  return item;
}

export function normalizeZhihuActivity(raw: unknown): ZhihuBootstrapItem | null {
  const activity = asRecord(raw);
  const action = str(activity.action_text) || str(activity.verb) || str(activity.action);
  if (!action.startsWith("赞同了") && !action.startsWith("喜欢了") && !action.startsWith("收藏了")) {
    return null;
  }

  const target = asRecord(activity.target);
  const contentType = str(target.type);
  if (contentType !== "answer" && contentType !== "article") return null;
  const fallbackUrl = absoluteZhihuUrl(str(target.url));
  const contentId = contentIdFromUrl(contentType, fallbackUrl) || str(target.id);
  if (!contentId) return null;

  const question = asRecord(target.question);
  const questionId = questionIdFromUrl(fallbackUrl) || str(question.id);
  const url =
    contentType === "answer"
      ? answerUrl(questionId, contentId, fallbackUrl)
      : articleUrl(contentId, fallbackUrl);
  if (!url) return null;

  const author = asRecord(target.author);
  const title =
    contentType === "answer"
      ? str(question.title) || `answer_${contentId}`
      : str(target.title) || `article_${contentId}`;

  const item: ZhihuBootstrapItem = {
    scope: "zhihu_activity",
    content_type: contentType,
    content_id: contentId,
    title,
    author: str(author.name),
    url,
    interaction_action: action,
  };
  if (questionId) item.question_id = questionId;
  const voteup = num(target.voteup_count);
  if (voteup !== undefined) item.voteup = voteup;
  const activityId = str(activity.id);
  if (activityId) item.interaction_time = activityId;
  return item;
}

export function normalizeZhihuCollectionItem(
  raw: unknown,
  collection: ZhihuCollectionMeta,
): ZhihuBootstrapItem | null {
  const row = asRecord(raw);
  const content = asRecord(row.content || raw);
  const contentType = str(content.type);
  if (contentType !== "answer" && contentType !== "article") return null;
  const fallbackUrl = absoluteZhihuUrl(str(content.url));
  const contentId = contentIdFromUrl(contentType, fallbackUrl) || str(content.id);
  if (!contentId) return null;

  const question = asRecord(content.question);
  const questionId = questionIdFromUrl(fallbackUrl) || str(question.id);
  const url =
    contentType === "answer"
      ? answerUrl(questionId, contentId, fallbackUrl) || fallbackUrl
      : articleUrl(contentId, fallbackUrl);
  if (!url) return null;

  const author = asRecord(content.author);
  const title =
    contentType === "answer"
      ? str(question.title) || str(content.title) || `answer_${contentId}`
      : str(content.title) || `article_${contentId}`;

  const item: ZhihuBootstrapItem = {
    scope: "zhihu_collection",
    content_type: contentType,
    content_id: contentId,
    title,
    author: str(author.name),
    url,
    collection_id: collection.id,
    collection_name: collection.name,
  };
  if (questionId) item.question_id = questionId;
  const summary = str(content.excerpt) || str(content.summary);
  if (summary) item.summary = summary;
  const voteup = num(content.voteup_count);
  if (voteup !== undefined) item.voteup = voteup;
  return item;
}

export function normalizeZhihuSearchResult(
  raw: unknown,
  keyword: string,
  sourceKeywordId?: number,
): ZhihuBootstrapItem | null {
  const item = normalizeZhihuDiscoveryObject(raw, "zhihu_search", "zhihu-search");
  if (!item) return null;
  item.search_keyword = keyword;
  if (typeof sourceKeywordId === "number" && Number.isFinite(sourceKeywordId)) {
    item.source_keyword_id = sourceKeywordId;
  }
  return item;
}

function normalizeZhihuDiscoveryObject(
  raw: unknown,
  scope: ZhihuScope,
  sourceStrategy: string,
  fallbackAuthorUrl = "",
): ZhihuBootstrapItem | null {
  const row = asRecord(raw);
  const object = asRecord(row.object || row.target || row.content || raw);
  let contentType = str(object.type) || str(row.content_type) || str(row.type);
  if (contentType === "search_result") contentType = str(object.type);
  if (contentType !== "answer" && contentType !== "article" && contentType !== "question") {
    return null;
  }

  const fallbackUrl = absoluteZhihuUrl(str(object.url) || str(row.url));
  const contentId =
    contentIdFromUrl(contentType, fallbackUrl) || str(object.id) || str(row.id);
  if (!contentId) return null;
  const question = asRecord(object.question);
  const questionId =
    contentType === "question" ? contentId : questionIdFromUrl(fallbackUrl) || str(question.id);
  const url =
    contentType === "answer"
      ? answerUrl(questionId, contentId, fallbackUrl) || fallbackUrl
      : contentType === "article"
        ? articleUrl(contentId, fallbackUrl)
        : questionUrl(contentId, fallbackUrl);
  if (!url) return null;

  const author = asRecord(object.author);
  const authorUrl = peopleUrlFromAuthor(author, fallbackAuthorUrl);
  const title =
    contentType === "answer"
      ? stripHtml(str(question.title) || str(row.title) || `answer_${contentId}`)
      : stripHtml(str(object.title) || str(row.title) || `zhihu_${contentId}`);
  const summary = stripHtml(str(object.excerpt) || str(object.summary) || str(row.excerpt));

  const item: ZhihuBootstrapItem = {
    scope,
    content_type: contentType,
    content_id: contentId,
    title,
    author: str(author.name) || str(author.url_token),
    url,
    source_strategy: sourceStrategy,
  };
  if (authorUrl) item.author_url = authorUrl;
  if (questionId && contentType !== "question") item.question_id = questionId;
  if (summary) item.summary = summary;
  const voteup = num(object.voteup_count ?? row.voteup_count);
  if (voteup !== undefined) item.voteup = voteup;
  const favoriteCount = num(object.favorite_count ?? object.collect_count ?? row.favorite_count);
  if (favoriteCount !== undefined) item.favorite_count = favoriteCount;
  const commentCount = num(object.comment_count ?? row.comment_count);
  if (commentCount !== undefined) item.comment_count = commentCount;
  const answerCount = num(object.answer_count ?? row.answer_count);
  if (commentCount === undefined && answerCount !== undefined) item.comment_count = answerCount;
  const followerCount = num(object.follower_count ?? row.follower_count);
  if (favoriteCount === undefined && followerCount !== undefined) item.favorite_count = followerCount;
  return item;
}

export function normalizeZhihuHotItem(raw: unknown): ZhihuBootstrapItem | null {
  return normalizeZhihuDiscoveryObject(raw, "zhihu_hot", "zhihu-hot");
}

export function normalizeZhihuFeedItem(raw: unknown): ZhihuBootstrapItem | null {
  return normalizeZhihuDiscoveryObject(raw, "zhihu_feed", "zhihu-feed");
}

export function normalizeZhihuCreatorItem(
  raw: unknown,
  creatorUrl: string,
): ZhihuBootstrapItem | null {
  return normalizeZhihuDiscoveryObject(raw, "zhihu_creator", "zhihu-creator", creatorUrl);
}

export function normalizeZhihuRelatedItem(
  raw: unknown,
  seedUrl: string,
): ZhihuBootstrapItem | null {
  return normalizeZhihuDiscoveryObject(raw, "zhihu_related", "zhihu-related", seedUrl);
}

async function fetchWithTimeout(url: string): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), ZHIHU_FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, { credentials: "include", signal: controller.signal });
  } finally {
    clearTimeout(timeoutId);
  }
}

async function fetchJson(url: string): Promise<Record<string, unknown>> {
  const response = await fetchWithTimeout(url);
  if (!response.ok) throw new ZhihuHttpError(response.status, await response.text(), url);
  return asRecord(parseJsonPreservingLargeIntegers(await response.text()));
}

async function fetchText(url: string): Promise<string> {
  const response = await fetchWithTimeout(url);
  if (!response.ok) throw new ZhihuHttpError(response.status, await response.text(), url);
  return await response.text();
}

async function fetchReadHistory(maxItems: number): Promise<ZhihuBootstrapItem[]> {
  const out: ZhihuBootstrapItem[] = [];
  let offset = 0;
  const limit = 20;
  for (let i = 0; i < 20 && out.length < maxItems; i++) {
    const payload = await fetchJson(
      `/api/v4/unify-consumption/read_history?offset=${offset}&limit=${limit}`,
    );
    const data = Array.isArray(payload.data) ? payload.data : [];
    for (const row of data) {
      const item = normalizeZhihuReadHistory(row);
      if (item) out.push(item);
      if (out.length >= maxItems) break;
    }
    const paging = asRecord(payload.paging);
    if (paging.is_end === true || data.length === 0) break;
    offset += limit;
  }
  return out;
}

async function fetchSearchResults(
  keyword: string,
  maxItems: number,
  sourceKeywordId?: number,
): Promise<ZhihuBootstrapItem[]> {
  const query = keyword.trim();
  if (!query) return [];
  const out: ZhihuBootstrapItem[] = [];
  let offset = 0;
  const limit = Math.min(20, Math.max(1, maxItems));
  for (let i = 0; i < 20 && out.length < maxItems; i++) {
    const payload = await fetchJson(
      `/api/v4/search_v3?t=general&q=${encodeURIComponent(query)}&correction=1&offset=${offset}&limit=${limit}`,
    );
    const data = Array.isArray(payload.data) ? payload.data : [];
    for (const row of data) {
      const item = normalizeZhihuSearchResult(row, query, sourceKeywordId);
      if (item) out.push(item);
      if (out.length >= maxItems) break;
    }
    const paging = asRecord(payload.paging);
    if (paging.is_end === true || data.length === 0) break;
    offset += limit;
  }
  return out;
}

async function fetchHotItems(maxItems: number): Promise<ZhihuBootstrapItem[]> {
  const out: ZhihuBootstrapItem[] = [];
  const limit = Math.min(50, Math.max(1, maxItems));
  const payload = await fetchJson(`/api/v3/feed/topstory/hot-lists/total?limit=${limit}&desktop=true`);
  const data = Array.isArray(payload.data) ? payload.data : [];
  for (const row of data) {
    const item = normalizeZhihuHotItem(row);
    if (item) out.push(item);
    if (out.length >= maxItems) break;
  }
  return out;
}

async function fetchFeedItems(maxItems: number): Promise<ZhihuBootstrapItem[]> {
  const out: ZhihuBootstrapItem[] = [];
  const limit = Math.min(20, Math.max(1, maxItems));
  let afterId = "0";
  for (let i = 0; i < 10 && out.length < maxItems; i++) {
    const payload = await fetchJson(
      `/api/v3/feed/topstory/recommend?desktop=true&limit=${limit}&action=down&after_id=${encodeURIComponent(afterId)}`,
    );
    const data = Array.isArray(payload.data) ? payload.data : [];
    for (const row of data) {
      const item = normalizeZhihuFeedItem(row);
      if (item) out.push(item);
      if (out.length >= maxItems) break;
    }
    const paging = asRecord(payload.paging);
    const next = str(paging.next);
    const nextAfter = next.match(/[?&]after_id=([^&]+)/)?.[1] ?? "";
    if (paging.is_end === true || data.length === 0 || !nextAfter) break;
    afterId = decodeURIComponent(nextAfter);
  }
  return out;
}

function creatorSlugFromUrl(url: string): string {
  return extractPeopleSlug(url) || str(url).replace(/^@/, "");
}

async function fetchCreatorItems(
  creatorUrls: string[],
  maxItemsPerCreator: number,
): Promise<ZhihuBootstrapItem[]> {
  const out: ZhihuBootstrapItem[] = [];
  for (const creatorUrl of creatorUrls) {
    const slug = creatorSlugFromUrl(creatorUrl);
    if (!slug) continue;
    for (const branch of ["articles", "answers"]) {
      let offset = 0;
      const limit = Math.min(20, Math.max(1, maxItemsPerCreator));
      for (let i = 0; i < 10 && out.length < creatorUrls.length * maxItemsPerCreator; i++) {
        const payload = await fetchJson(
          `/api/v4/members/${encodeURIComponent(slug)}/${branch}?offset=${offset}&limit=${limit}`,
        );
        const data = Array.isArray(payload.data) ? payload.data : [];
        for (const row of data) {
          const item = normalizeZhihuCreatorItem(row, creatorUrl);
          if (item) out.push(item);
          if (out.length >= creatorUrls.length * maxItemsPerCreator) break;
        }
        const paging = asRecord(payload.paging);
        if (paging.is_end === true || data.length === 0) break;
        offset += limit;
      }
    }
  }
  return out.slice(0, creatorUrls.length * maxItemsPerCreator);
}

async function fetchRelatedItems(
  relatedUrls: string[],
  maxItemsPerSeed: number,
): Promise<ZhihuBootstrapItem[]> {
  const out: ZhihuBootstrapItem[] = [];
  for (const seedUrl of relatedUrls) {
    const questionId = questionIdFromUrl(seedUrl);
    if (!questionId) continue;
    let offset = 0;
    const limit = Math.min(20, Math.max(1, maxItemsPerSeed));
    for (let i = 0; i < 10 && out.length < relatedUrls.length * maxItemsPerSeed; i++) {
      const payload = await fetchJson(
        `/api/v4/questions/${encodeURIComponent(questionId)}/feeds?offset=${offset}&limit=${limit}`,
      );
      const data = Array.isArray(payload.data) ? payload.data : [];
      for (const row of data) {
        const item = normalizeZhihuRelatedItem(row, seedUrl);
        if (item) out.push(item);
        if (out.length >= relatedUrls.length * maxItemsPerSeed) break;
      }
      const paging = asRecord(payload.paging);
      if (paging.is_end === true || data.length === 0) break;
      offset += limit;
    }
  }
  return out.slice(0, relatedUrls.length * maxItemsPerSeed);
}

async function fetchActivity(
  profileSlug: string,
  maxItems: number,
): Promise<ZhihuBootstrapItem[]> {
  if (!profileSlug) return [];
  const urls = [
    `/api/v3/moments/${encodeURIComponent(profileSlug)}/activities?limit=10&desktop=true&ws_qiangzhisafe=0`,
    `/api/v4/members/${encodeURIComponent(profileSlug)}/activities?limit=10&desktop=true`,
  ];
  let lastError: unknown = null;
  for (const url of urls) {
    try {
      return await fetchActivityFromUrl(url, maxItems);
    } catch (error) {
      if (isLoginRequiredError(error)) throw error;
      lastError = error;
    }
  }
  if (lastError) throw lastError;
  return [];
}

async function fetchActivityFromUrl(
  startUrl: string,
  maxItems: number,
): Promise<ZhihuBootstrapItem[]> {
  const out: ZhihuBootstrapItem[] = [];
  let nextUrl = startUrl;
  let likeCount = 0;
  let favoriteCount = 0;
  for (
    let i = 0;
    i < 40 && (likeCount < maxItems || favoriteCount < maxItems) && nextUrl;
    i++
  ) {
    const payload = await fetchJson(nextUrl);
    const data = Array.isArray(payload.data) ? payload.data : [];
    for (const row of data) {
      const item = normalizeZhihuActivity(row);
      if (!item) continue;
      const action = item.interaction_action ?? "";
      if ((action.startsWith("赞同了") || action.startsWith("喜欢了")) && likeCount < maxItems) {
        out.push(item);
        likeCount += 1;
      } else if (action.startsWith("收藏了") && favoriteCount < maxItems) {
        out.push(item);
        favoriteCount += 1;
      }
      if (likeCount >= maxItems && favoriteCount >= maxItems) break;
    }
    const paging = asRecord(payload.paging);
    if (paging.is_end === true || data.length === 0) break;
    nextUrl = str(paging.next);
  }
  return out;
}

function normalizeCollectionMeta(raw: unknown): ZhihuCollectionMeta | null {
  const row = asRecord(raw);
  const id = str(row.id);
  if (!id) return null;
  const name = str(row.title) || str(row.name) || `collection_${id}`;
  return { id, name };
}

async function fetchMemberCollections(
  profileSlug: string,
  maxCollections: number,
): Promise<ZhihuCollectionMeta[]> {
  const collections: ZhihuCollectionMeta[] = [];
  const seen = new Set<string>();
  let offset = 0;
  const limit = 20;
  for (let i = 0; i < 20 && collections.length < maxCollections; i++) {
    const payload = await fetchJson(
      `/api/v4/members/${encodeURIComponent(profileSlug)}/favlists?offset=${offset}&limit=${limit}`,
    );
    const data = Array.isArray(payload.data) ? payload.data : [];
    for (const row of data) {
      const collection = normalizeCollectionMeta(row);
      if (!collection || seen.has(collection.id)) continue;
      seen.add(collection.id);
      collections.push(collection);
      if (collections.length >= maxCollections) break;
    }
    const paging = asRecord(payload.paging);
    if (paging.is_end === true || data.length === 0) break;
    offset += limit;
  }
  return collections;
}

function extractCollectionsFromHtml(html: string, maxCollections: number): ZhihuCollectionMeta[] {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const seen = new Set<string>();
  const collections: ZhihuCollectionMeta[] = [];
  const anchors = Array.from(doc.querySelectorAll<HTMLAnchorElement>('a[href*="/collection/"]'));
  for (const anchor of anchors) {
    const match = anchor.href.match(/\/collection\/(\d+)/);
    const id = match?.[1] ?? "";
    if (!id || seen.has(id)) continue;
    seen.add(id);
    collections.push({ id, name: (anchor.textContent ?? "").trim() || `collection_${id}` });
    if (collections.length >= maxCollections) break;
  }
  return collections;
}

async function fetchCollections(
  maxItems: number,
  maxCollections: number,
  profileSlug: string,
): Promise<ZhihuCollectionFetchResult> {
  const all: ZhihuBootstrapItem[] = [];
  const debug: Record<string, unknown> = {};
  let collections: ZhihuCollectionMeta[] = [];

  if (profileSlug) {
    try {
      collections = await fetchMemberCollections(profileSlug, maxCollections);
      debug.zhihu_collection_source = "favlists_api";
    } catch (error) {
      if (isLoginRequiredError(error)) throw error;
      debug.zhihu_collection_api_error = error instanceof Error ? error.message : String(error);
    }
  }

  if (collections.length === 0) {
    debug.zhihu_collection_source = profileSlug ? "html_fallback" : "html";
    for (let page = 1; page <= 20 && collections.length < maxCollections; page++) {
      const html = await fetchText(`/collections/mine?page=${page}`);
      const pageCollections = extractCollectionsFromHtml(html, maxCollections - collections.length);
      debug[`zhihu_collection_html_page_${page}`] = pageCollections.length;
      if (pageCollections.length === 0) break;
      collections.push(...pageCollections);
    }
  }

  debug.zhihu_collection_count = collections.length;
  for (const collection of collections) {
    let offset = 0;
    const limit = 20;
    for (let i = 0; i < 20 && all.length < maxItems; i++) {
      let payload: Record<string, unknown>;
      try {
        payload = await fetchJson(
          `/api/v4/favlists/${collection.id}/items?offset=${offset}&limit=${limit}`,
        );
      } catch (error) {
        if (isLoginRequiredError(error)) throw error;
        payload = await fetchJson(
          `/api/v4/collections/${collection.id}/items?offset=${offset}&limit=${limit}`,
        );
      }
      const data = Array.isArray(payload.data) ? payload.data : [];
      for (const row of data) {
        const item = normalizeZhihuCollectionItem(row, collection);
        if (item) all.push(item);
        if (all.length >= maxItems) break;
      }
      const paging = asRecord(payload.paging);
      if (paging.is_end === true || data.length === 0) break;
      offset += limit;
    }
    if (all.length >= maxItems) break;
  }
  debug.zhihu_collection = all.length;
  return { items: all, debug };
}

function dedupeItems(items: ZhihuBootstrapItem[]): ZhihuBootstrapItem[] {
  const seen = new Set<string>();
  const out: ZhihuBootstrapItem[] = [];
  for (const item of items) {
    const key = `${item.scope}:${item.content_type}:${item.content_id || item.url}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

function countItems(items: ZhihuBootstrapItem[]): Record<string, number> {
  const counts: Record<string, number> = {
    zhihu_read_history: 0,
    zhihu_collection: 0,
    zhihu_activity_like: 0,
    zhihu_activity_favorite: 0,
    zhihu_search: 0,
    zhihu_hot: 0,
    zhihu_feed: 0,
    zhihu_creator: 0,
    zhihu_related: 0,
  };
  for (const item of items) {
    if (item.scope === "zhihu_read_history") counts.zhihu_read_history += 1;
    if (item.scope === "zhihu_collection") counts.zhihu_collection += 1;
    if (item.scope === "zhihu_activity") {
      const action = item.interaction_action ?? "";
      if (action.startsWith("赞同了") || action.startsWith("喜欢了")) {
        counts.zhihu_activity_like += 1;
      }
      if (action.startsWith("收藏了")) counts.zhihu_activity_favorite += 1;
    }
    if (item.scope === "zhihu_search") counts.zhihu_search += 1;
    if (item.scope === "zhihu_hot") counts.zhihu_hot += 1;
    if (item.scope === "zhihu_feed") counts.zhihu_feed += 1;
    if (item.scope === "zhihu_creator") counts.zhihu_creator += 1;
    if (item.scope === "zhihu_related") counts.zhihu_related += 1;
  }
  return counts;
}

export async function executeZhihuTask(msg: ZhihuExecuteMessage): Promise<ZhihuTaskResult> {
  const taskId = msg.task_id;
  if (msg.type === "search") {
    return executeZhihuSearchTask(msg);
  }
  if (msg.type === "hot" || msg.type === "feed" || msg.type === "creator" || msg.type === "related") {
    return executeZhihuDiscoveryTask(msg);
  }
  const scopes = msg.scopes && msg.scopes.length > 0 ? msg.scopes : [...DEFAULT_SCOPES];
  const maxItems = Math.max(1, Math.floor(msg.max_items_per_scope ?? 300));
  const maxCollections = Math.max(1, Math.floor(msg.max_collections ?? 20));
  const items: ZhihuBootstrapItem[] = [];
  const debug: Record<string, unknown> = {};

  try {
    let currentMember: ZhihuMemberMeta | null = null;
    if (!str(msg.profile_slug) && (scopes.includes("zhihu_activity") || scopes.includes("zhihu_collection"))) {
      currentMember = await fetchCurrentMember();
      if (currentMember?.urlToken) {
        debug.current_member_url_token = currentMember.urlToken;
      }
    }
    const profileSlug = str(msg.profile_slug) || currentMember?.urlToken || "";
    if (scopes.includes("zhihu_read_history")) {
      const rows = await fetchReadHistory(maxItems);
      debug.zhihu_read_history = rows.length;
      items.push(...rows);
    }
    if (scopes.includes("zhihu_activity")) {
      try {
        const rows = await fetchActivity(profileSlug, maxItems);
        debug.zhihu_activity = rows.length;
        items.push(...rows);
      } catch (error) {
        if (isLoginRequiredError(error)) throw error;
        debug.zhihu_activity = 0;
        debug.zhihu_activity_error = error instanceof Error ? error.message : String(error);
        if (error instanceof ZhihuHttpError) {
          debug.zhihu_activity_request_url = error.requestUrl;
          debug.zhihu_activity_http_status = error.status;
          debug.zhihu_activity_response_body = error.body.slice(0, 300);
        }
      }
    }
    if (scopes.includes("zhihu_collection")) {
      try {
        const result = await fetchCollections(maxItems, maxCollections, profileSlug);
        Object.assign(debug, result.debug);
        items.push(...result.items);
      } catch (error) {
        if (isLoginRequiredError(error)) throw error;
        debug.zhihu_collection = 0;
        debug.zhihu_collection_error = error instanceof Error ? error.message : String(error);
        if (error instanceof ZhihuHttpError) {
          debug.zhihu_collection_request_url = error.requestUrl;
          debug.zhihu_collection_http_status = error.status;
          debug.zhihu_collection_response_body = error.body.slice(0, 300);
        }
      }
    }
    const branchCount =
      (scopes.includes("zhihu_read_history") ? 1 : 0) +
      (scopes.includes("zhihu_collection") ? 1 : 0) +
      (scopes.includes("zhihu_activity") ? 2 : 0);
    const deduped = dedupeItems(items).slice(0, maxItems * Math.max(1, branchCount));
    return {
      task_id: taskId,
      status: deduped.length > 0 ? "ok" : "empty",
      items: deduped,
      scope_counts: countItems(deduped),
      debug,
    };
  } catch (error) {
    if (isLoginRequiredError(error)) {
      debug.login_required = true;
      debug.current_url = currentUrl();
      debug.http_status = error.status;
      debug.request_url = error.requestUrl;
      debug.response_body = error.body.slice(0, 300);
      return {
        task_id: taskId,
        status: "failed",
        items: [],
        scope_counts: countItems([]),
        error: "zhihu_login_required",
        debug,
      };
    }
    if (error instanceof ZhihuHttpError) {
      debug.request_url = error.requestUrl;
      debug.http_status = error.status;
      debug.response_body = error.body.slice(0, 300);
    }
    return {
      task_id: taskId,
      status: "failed",
      items: [],
      scope_counts: countItems([]),
      error: error instanceof Error ? error.message : String(error),
      debug,
    };
  }
}

async function executeZhihuDiscoveryTask(msg: ZhihuExecuteMessage): Promise<ZhihuTaskResult> {
  const taskId = msg.task_id;
  const maxItems = Math.max(1, Math.floor(msg.max_items ?? msg.max_items_per_scope ?? 20));
  const items: ZhihuBootstrapItem[] = [];
  const debug: Record<string, unknown> = { task_type: msg.type };

  try {
    if (msg.type === "hot") {
      const rows = await fetchHotItems(maxItems);
      debug.zhihu_hot = rows.length;
      items.push(...rows);
    } else if (msg.type === "feed") {
      const rows = await fetchFeedItems(maxItems);
      debug.zhihu_feed = rows.length;
      items.push(...rows);
    } else if (msg.type === "creator") {
      const creatorUrls = Array.isArray(msg.creator_urls)
        ? msg.creator_urls.map((item) => str(item)).filter(Boolean)
        : [];
      const rows = await fetchCreatorItems(
        creatorUrls,
        Math.max(1, Math.floor(msg.max_items_per_creator ?? maxItems)),
      );
      debug.creator_urls = creatorUrls;
      debug.zhihu_creator = rows.length;
      items.push(...rows);
    } else if (msg.type === "related") {
      const relatedUrls = Array.isArray(msg.related_urls)
        ? msg.related_urls.map((item) => str(item)).filter(Boolean)
        : [];
      const rows = await fetchRelatedItems(
        relatedUrls,
        Math.max(1, Math.floor(msg.max_items_per_seed ?? maxItems)),
      );
      debug.related_urls = relatedUrls;
      debug.zhihu_related = rows.length;
      items.push(...rows);
    }
    const deduped = dedupeItems(items);
    return {
      task_id: taskId,
      status: deduped.length > 0 ? "ok" : "empty",
      items: deduped,
      scope_counts: countItems(deduped),
      debug,
    };
  } catch (error) {
    if (isLoginRequiredError(error)) {
      debug.login_required = true;
      debug.current_url = currentUrl();
      debug.http_status = error.status;
      debug.request_url = error.requestUrl;
      debug.response_body = error.body.slice(0, 300);
      return {
        task_id: taskId,
        status: "failed",
        items: [],
        scope_counts: countItems([]),
        error: "zhihu_login_required",
        debug,
      };
    }
    if (error instanceof ZhihuHttpError) {
      debug.request_url = error.requestUrl;
      debug.http_status = error.status;
      debug.response_body = error.body.slice(0, 300);
    }
    return {
      task_id: taskId,
      status: "failed",
      items: [],
      scope_counts: countItems([]),
      error: error instanceof Error ? error.message : String(error),
      debug,
    };
  }
}

async function executeZhihuSearchTask(msg: ZhihuExecuteMessage): Promise<ZhihuTaskResult> {
  const taskId = msg.task_id;
  const keywords = Array.isArray(msg.keywords)
    ? msg.keywords.map((item) => str(item)).filter(Boolean)
    : [];
  const maxItems = Math.max(1, Math.floor(msg.max_items_per_keyword ?? msg.max_items_per_scope ?? 20));
  const sourceKeywordIds = isRecord(msg.source_keyword_ids) ? msg.source_keyword_ids : {};
  const items: ZhihuBootstrapItem[] = [];
  const debug: Record<string, unknown> = { keywords };

  try {
    for (const keyword of keywords) {
      const sourceKeywordId = num(sourceKeywordIds[keyword]);
      const rows = await fetchSearchResults(keyword, maxItems, sourceKeywordId);
      debug[`zhihu_search_${keyword}`] = rows.length;
      items.push(...rows);
    }
    const deduped = dedupeItems(items);
    return {
      task_id: taskId,
      status: deduped.length > 0 ? "ok" : "empty",
      items: deduped,
      scope_counts: countItems(deduped),
      debug,
    };
  } catch (error) {
    if (isLoginRequiredError(error)) {
      debug.login_required = true;
      debug.current_url = currentUrl();
      debug.http_status = error.status;
      debug.request_url = error.requestUrl;
      debug.response_body = error.body.slice(0, 300);
      return {
        task_id: taskId,
        status: "failed",
        items: [],
        scope_counts: countItems([]),
        error: "zhihu_login_required",
        debug,
      };
    }
    if (error instanceof ZhihuHttpError) {
      debug.request_url = error.requestUrl;
      debug.http_status = error.status;
      debug.response_body = error.body.slice(0, 300);
    }
    return {
      task_id: taskId,
      status: "failed",
      items: [],
      scope_counts: countItems([]),
      error: error instanceof Error ? error.message : String(error),
      debug,
    };
  }
}

export function installZhihuMessageListener(): void {
  chrome.runtime.onMessage.addListener(
    (
      message: { action?: string; data?: ZhihuExecuteMessage },
      _sender,
      sendResponse,
    ) => {
      if (message.action !== "ZHIHU_BOOTSTRAP_EXECUTE" && message.action !== "ZHIHU_TASK_EXECUTE") return false;
      void executeZhihuTask(message.data as ZhihuExecuteMessage).then((result) => {
        chrome.runtime.sendMessage({ action: "ZHIHU_TASK_RESULT", data: result });
        sendResponse({ ok: true });
      });
      return true;
    },
  );
}
