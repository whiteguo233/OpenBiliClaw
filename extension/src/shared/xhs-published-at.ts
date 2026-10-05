/**
 * Exact Xiaohongshu publication-time extraction.
 *
 * Real logged-in evidence captured 2026-09:
 *  - `/api/sns/web/v2/search/notes` cards and `note/collect/page` cards carry
 *    NO time field at all;
 *  - profile `/api/sns/web/v1/user_posted` items carry `time` (epoch ms);
 *  - a note page's `__INITIAL_STATE__.note.noteDetailMap[noteId].note` carries
 *    `time` (epoch ms) after the SPA hydrates. A hidden same-origin iframe
 *    loaded from a search card URL therefore exposes the exact time without
 *    leaving the task tab.
 *
 * Everything here is pure and content-free: only note ids and timestamps are
 * read, never titles, authors, URLs or note bodies.
 */

export interface XhsPublishedTime {
  note_id: string;
  published_at: number;
}

const NOTE_ID_RE = /^[0-9a-f]{24}$/i;
const NOTE_ID_KEYS = ["note_id", "noteId", "noteID", "id"] as const;
const TIME_KEYS = [
  "time",
  "publishTime",
  "publish_time",
  "createTime",
  "create_time",
  "publishedAt",
  "published_at",
  "lastUpdateTime",
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function pickNoteId(record: Record<string, unknown>): string {
  for (const key of NOTE_ID_KEYS) {
    const value = record[key];
    if (typeof value === "string" && NOTE_ID_RE.test(value)) return value;
  }
  return "";
}

function pickTime(record: Record<string, unknown> | undefined): number | undefined {
  if (!record) return undefined;
  for (const key of TIME_KEYS) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value) && value >= 1e12 && value < 1e14) {
      return Math.floor(value);
    }
    if (typeof value === "string" && /^\d{13}$/.test(value)) return Number(value);
  }
  return undefined;
}

export function extractNoteIdFromUrl(rawUrl: string): string {
  if (!rawUrl) return "";
  try {
    const pathname = new URL(rawUrl, "https://www.xiaohongshu.com").pathname;
    const match = pathname.match(/\/(?:explore|discovery\/item)\/([0-9a-fA-F]{24})/);
    return match ? match[1] : "";
  } catch {
    return "";
  }
}

/**
 * Collect every `(note_id, epoch-ms time)` pair exposed anywhere in one XHS API
 * payload. Covers `user_posted` (`data.notes[].time`) and any future card shape
 * that nests the timestamp under `note_card`.
 */
export function extractPublishedTimesFromPayload(payload: unknown): XhsPublishedTime[] {
  const found = new Map<string, number>();
  const seen = new WeakSet<object>();
  let visited = 0;

  function walk(node: unknown, depth: number): void {
    if (!node || typeof node !== "object" || depth > 8 || visited > 60_000) return;
    if (seen.has(node)) return;
    seen.add(node);
    visited += 1;
    if (Array.isArray(node)) {
      for (const item of node.slice(0, 80)) walk(item, depth + 1);
      return;
    }
    const record = node as Record<string, unknown>;
    const noteId = pickNoteId(record);
    if (noteId) {
      const direct = pickTime(record);
      if (direct !== undefined && !found.has(noteId)) found.set(noteId, direct);
      const card = record.note_card ?? record.noteCard;
      if (isRecord(card)) {
        const nested = pickTime(card);
        if (nested !== undefined && !found.has(noteId)) found.set(noteId, nested);
      }
    }
    for (const value of Object.values(record)) walk(value, depth + 1);
  }

  walk(payload, 0);
  return [...found.entries()].map(([note_id, published_at]) => ({ note_id, published_at }));
}

/**
 * Read the exact publish time from a hydrated `__INITIAL_STATE__` note store.
 * Falls back to `lastUpdateTime` only when `time` is absent.
 */
export function extractPublishedAtFromState(state: unknown, noteId: string): number | undefined {
  if (!isRecord(state) || !noteId) return undefined;
  const noteStore = state.note;
  if (!isRecord(noteStore)) return undefined;
  const noteDetailMap = noteStore.noteDetailMap;
  if (!isRecord(noteDetailMap)) return undefined;
  const entry = noteDetailMap[noteId];
  if (!isRecord(entry)) return undefined;
  const note = isRecord(entry.note)
    ? entry.note
    : isRecord(entry.noteCard)
      ? entry.noteCard
      : entry;
  return pickTime(note) ?? pickTime(entry);
}

/**
 * Merge sniffer-reported `(note_id, time)` pairs into collected notes. Notes
 * without a `note_id` fall back to the id embedded in their URL.
 */
export function mergePublishedTimes<
  T extends { note_id?: string; url?: string; published_at?: string | number },
>(notes: T[], times: readonly XhsPublishedTime[]): number {
  if (times.length === 0) return 0;
  const byId = new Map(times.map((item) => [item.note_id, item.published_at]));
  let merged = 0;
  for (const note of notes) {
    if (note.published_at) continue;
    const noteId =
      (typeof note.note_id === "string" && note.note_id) || extractNoteIdFromUrl(note.url ?? "");
    const publishedAt = byId.get(noteId);
    if (publishedAt === undefined) continue;
    note.published_at = publishedAt;
    merged += 1;
  }
  return merged;
}
