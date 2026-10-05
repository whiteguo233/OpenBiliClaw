/**
 * Fill exact Xiaohongshu publish times from a hydrated note page.
 *
 * Search / collect cards expose no time field, so when the backend flags a
 * non-"all" date preference the task executor asks the MAIN-world state bridge
 * to load up to five note URLs in hidden same-origin iframes and read
 * `__INITIAL_STATE__.note.noteDetailMap[noteId].note.time` (epoch ms).
 *
 * The content script itself runs in an isolated world and cannot read the
 * page's `__INITIAL_STATE__`, so the iframe work happens in the MAIN world and
 * only `(note_id, published_at)` crosses back via postMessage. Failures are
 * best-effort and never fail the task.
 */

import {
  extractNoteIdFromUrl,
  type XhsPublishedTime,
} from "../../shared/xhs-published-at.ts";

const REQUEST_SOURCE = "obc-xhs-note-time-request";
const RESULT_SOURCE = "obc-xhs-note-time-result";
const MAX_CONCURRENCY = 3;

export type PublishedAtLoader = (
  url: string,
  noteId: string,
  timeoutMs: number,
) => Promise<number | undefined>;

export interface EnrichPublishedAtStats {
  targets: number;
  attempted: number;
  enriched: number;
}

export interface EnrichPublishedAtOptions {
  maxNotes?: number;
  timeoutMs?: number;
  concurrency?: number;
  loader?: PublishedAtLoader;
  stats?: EnrichPublishedAtStats;
}

export function isXhsNoteUrl(rawUrl: string): boolean {
  if (!rawUrl) return false;
  try {
    const parsed = new URL(rawUrl, "https://www.xiaohongshu.com");
    if (!parsed.hostname.endsWith("xiaohongshu.com")) return false;
    return Boolean(extractNoteIdFromUrl(parsed.href));
  } catch {
    return false;
  }
}

/**
 * Ask the MAIN-world bridge to resolve one note's exact publish time.
 * The bridge answers with `obc-xhs-note-time-result` carrying
 * `{request_id, note_id, published_at}`; null means not resolvable.
 */
export function requestNoteTimeViaBridge(
  url: string,
  noteId: string,
  timeoutMs: number,
): Promise<number | undefined> {
  return new Promise((resolve) => {
    const requestId = `obc-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    let settled = false;
    const finish = (publishedAt?: number): void => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      resolve(publishedAt);
    };
    const onMessage = (event: MessageEvent): void => {
      if (event.source !== window) return;
      const data = event.data as
        | { source?: string; request_id?: string; published_at?: number | null }
        | null;
      if (!data || data.source !== RESULT_SOURCE || data.request_id !== requestId) return;
      if (typeof data.published_at === "number") finish(data.published_at);
      else finish(undefined);
    };
    const timer = window.setTimeout(() => finish(undefined), timeoutMs + 1_000);
    window.addEventListener("message", onMessage);
    try {
      window.postMessage(
        {
          source: REQUEST_SOURCE,
          request_id: requestId,
          url,
          note_id: noteId,
          timeout_ms: timeoutMs,
        },
        "*",
      );
    } catch {
      finish(undefined);
    }
  });
}

/** Enrich notes concurrently under a hard note-count ceiling. */
export async function enrichNotesWithPublishedAt<
  T extends { url?: string; published_at?: string | number },
>(notes: T[], options: EnrichPublishedAtOptions = {}): Promise<number> {
  const maxNotes = Math.max(0, options.maxNotes ?? 5);
  const timeoutMs = Math.max(1_000, options.timeoutMs ?? 6_000);
  const concurrency = Math.max(1, Math.min(MAX_CONCURRENCY, options.concurrency ?? 2));
  const loader = options.loader ?? requestNoteTimeViaBridge;

  const targets = notes
    .filter((note) => !note.published_at && typeof note.url === "string" && isXhsNoteUrl(note.url))
    .slice(0, maxNotes);
  if (options.stats) options.stats.targets = targets.length;
  if (targets.length === 0) return 0;

  let cursor = 0;
  let enriched = 0;
  const worker = async (): Promise<void> => {
    while (cursor < targets.length) {
      const note = targets[cursor];
      cursor += 1;
      const noteId = extractNoteIdFromUrl(note.url ?? "");
      if (!noteId) continue;
      if (options.stats) options.stats.attempted += 1;
      const publishedAt = await loader(note.url as string, noteId, timeoutMs).catch(
        () => undefined,
      );
      if (publishedAt === undefined) continue;
      note.published_at = publishedAt;
      enriched += 1;
      if (options.stats) options.stats.enriched += 1;
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(concurrency, targets.length) }, () => worker()),
  );
  return enriched;
}

// Re-exported for callers that want the shared type without a second import.
export type { XhsPublishedTime };
