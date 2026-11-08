/**
 * Pure normalizer for Xiaohongshu web search API responses.
 *
 * Search result tabs are intentionally opened in the background. XHS may skip
 * mounting its virtualized card grid there, but the page still receives the
 * search JSON response. The MAIN-world bridge normalizes only the same public
 * card fields that the DOM collector already reports.
 */

export interface XhsSearchResponseNote {
  url: string;
  title: string;
  author: string;
  cover_url: string;
  description?: string;
  tags?: string[];
  view_count?: number;
  like_count?: number;
  collect_count?: number;
  comment_count?: number;
  published_at?: string | number;
}

const NOTE_ID_PATTERN = /^[0-9a-f]{24}$/i;
const NOTE_ID_KEYS = ["note_id", "noteId", "id"] as const;
const TOKEN_KEYS = ["xsec_token", "xsecToken"] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function firstString(
  records: readonly Record<string, unknown>[],
  keys: readonly string[],
): string {
  for (const record of records) {
    for (const key of keys) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  }
  return "";
}

function firstRecord(
  records: readonly Record<string, unknown>[],
  keys: readonly string[],
): Record<string, unknown> | null {
  for (const record of records) {
    for (const key of keys) {
      const value = record[key];
      if (isRecord(value)) return value;
    }
  }
  return null;
}

function firstCount(
  records: readonly Record<string, unknown>[],
  keys: readonly string[],
): number | undefined {
  for (const record of records) {
    for (const key of keys) {
      const value = record[key];
      if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
        return Math.floor(value);
      }
      if (typeof value === "string" && /^\d+$/.test(value.trim())) {
        const parsed = Number(value);
        if (Number.isSafeInteger(parsed)) return parsed;
      }
    }
  }
  return undefined;
}

function firstPublishedAt(
  records: readonly Record<string, unknown>[],
): string | number | undefined {
  const keys = ["time", "create_time", "createTime", "publish_time", "publishTime"];
  for (const record of records) {
    for (const key of keys) {
      const value = record[key];
      if (typeof value === "number" && Number.isFinite(value) && value > 0) return value;
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  }
  return undefined;
}

function noteIdentity(records: readonly Record<string, unknown>[]): string {
  for (const record of records) {
    for (const key of NOTE_ID_KEYS) {
      const value = record[key];
      if (typeof value === "string" && NOTE_ID_PATTERN.test(value)) return value;
    }
  }
  return "";
}

function hasNoteSpecificFields(record: Record<string, unknown>): boolean {
  return [
    "display_title",
    "displayTitle",
    "title",
    "desc",
    "cover",
    "interact_info",
    "interactInfo",
  ].some((key) => key in record);
}

function normalizeCandidate(raw: Record<string, unknown>): XhsSearchResponseNote | null {
  const wrapped = firstRecord([raw], ["note_card", "noteCard"]);
  const card = wrapped ?? raw;
  const records = [raw, card];
  const noteId = noteIdentity(records);
  if (!noteId || (!wrapped && !hasNoteSpecificFields(card))) return null;

  const token = firstString(records, TOKEN_KEYS);
  const params = new URLSearchParams();
  if (token) params.set("xsec_token", token);
  const query = params.toString();

  const authorRecord = firstRecord(records, ["user", "user_info", "userInfo", "author"]);
  const coverRecord = firstRecord(records, ["cover"]);
  const interactRecord = firstRecord(records, ["interact_info", "interactInfo"]);
  const metricRecords = interactRecord ? [interactRecord, card, raw] : [card, raw];

  const note: XhsSearchResponseNote = {
    url: `https://www.xiaohongshu.com/explore/${noteId}${query ? `?${query}` : ""}`,
    title: firstString(records, ["display_title", "displayTitle", "title", "desc"]),
    author: authorRecord
      ? firstString([authorRecord], ["nickname", "nick_name", "nickName", "name"])
      : "",
    cover_url: coverRecord
      ? firstString([coverRecord], [
          "url_default",
          "urlDefault",
          "url_pre",
          "urlPre",
          "url",
        ])
      : "",
  };

  const viewCount = firstCount(metricRecords, ["view_count", "viewCount"]);
  const likeCount = firstCount(metricRecords, ["liked_count", "likedCount", "like_count"]);
  const collectCount = firstCount(metricRecords, [
    "collected_count",
    "collectedCount",
    "collect_count",
  ]);
  const commentCount = firstCount(metricRecords, ["comment_count", "commentCount"]);
  const publishedAt = firstPublishedAt(records);
  // Only public fields of this note; never forward arbitrary response objects.
  const description = firstString([card], ["desc", "description"]).slice(0, 8000);
  if (description) note.description = description;
  const rawTags = card.tag_list ?? card.tagList;
  if (Array.isArray(rawTags)) {
    const tags = [...new Set(rawTags.slice(0, 32)
      .filter(isRecord)
      .map((tag) => firstString([tag], ["name"]).slice(0, 128))
      .filter(Boolean))];
    if (tags.length) note.tags = tags;
  }
  if (viewCount !== undefined) note.view_count = viewCount;
  if (likeCount !== undefined) note.like_count = likeCount;
  if (collectCount !== undefined) note.collect_count = collectCount;
  if (commentCount !== undefined) note.comment_count = commentCount;
  if (publishedAt !== undefined) note.published_at = publishedAt;
  return note;
}

/** Extract up to `limit` unique public note cards from a search response. */
export function extractXhsSearchResponseNotes(
  payload: unknown,
  limit: number = 20,
): XhsSearchResponseNote[] {
  const out: XhsSearchResponseNote[] = [];
  const seen = new Set<string>();
  const boundedLimit = Math.max(0, Math.floor(limit));

  function walk(node: unknown): void {
    if (out.length >= boundedLimit || node === null || typeof node !== "object") return;
    if (Array.isArray(node)) {
      for (const child of node) walk(child);
      return;
    }
    const record = node as Record<string, unknown>;
    const note = normalizeCandidate(record);
    if (note) {
      const key = new URL(note.url).pathname;
      if (!seen.has(key)) {
        seen.add(key);
        out.push(note);
      }
    }
    for (const value of Object.values(record)) walk(value);
  }

  walk(payload);
  return out;
}

export function isXhsSearchApiUrl(rawUrl: string): boolean {
  try {
    const url = new URL(rawUrl, "https://www.xiaohongshu.com/");
    return /^\/api\/sns\/web\/v\d+\/search\/notes(?:\/|$)/.test(url.pathname);
  } catch {
    return false;
  }
}
