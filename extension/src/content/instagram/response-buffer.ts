import type { InstagramWireItem } from "./task-executor.ts";
import {
  INSTAGRAM_REPLAY_EVENT,
  INSTAGRAM_RESPONSE_EVENT,
} from "../../shared/platforms/instagram.ts";

export { INSTAGRAM_REPLAY_EVENT, INSTAGRAM_RESPONSE_EVENT };

export interface InstagramObservedEnvelope {
  route: "topic" | "creator" | "unknown";
  collection_id?: string;
  items: InstagramWireItem[];
  observed_count?: number;
  rejected_count?: number;
  shape_valid?: boolean;
  cursor?: string;
  has_next_page?: boolean;
  affirmative_terminal?: boolean;
}

const MAX_ENVELOPES = 24;
const MAX_ITEMS_PER_ENVELOPE = 80;
const MAX_EVENT_BYTES = 256 * 1024;
const buffer: InstagramObservedEnvelope[] = [];

function boundedString(value: unknown, maximum: number): string {
  return typeof value === "string" ? value.trim().slice(0, maximum) : "";
}

function safeObservedItem(value: unknown): InstagramWireItem | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const candidate = value as Partial<InstagramWireItem>;
  const id = boundedString(candidate.id, 128);
  const url = boundedString(candidate.url, 4096);
  const contentType = candidate.content_type;
  if (!id || !url || !["post", "reel", "carousel"].includes(String(contentType))) return null;
  try {
    const parsed = new URL(url);
    if (
      parsed.protocol !== "https:"
      || (parsed.hostname !== "instagram.com" && !parsed.hostname.endsWith(".instagram.com"))
    ) return null;
  } catch {
    return null;
  }
  const coverUrl = boundedString(candidate.cover_url, 4096);
  let safeCover = "";
  if (coverUrl) {
    try {
      const parsed = new URL(coverUrl);
      const host = parsed.hostname.toLowerCase();
      if (
        parsed.protocol === "https:"
        && (host === "instagram.com"
          || host.endsWith(".instagram.com")
          || host.endsWith(".cdninstagram.com")
          || host.endsWith(".fbcdn.net"))
      ) safeCover = parsed.href;
    } catch {
      // Omit non-CDN bridge URLs.
    }
  }
  return {
    id,
    ...(boundedString(candidate.code, 128) ? { code: boundedString(candidate.code, 128) } : {}),
    content_type: contentType as InstagramWireItem["content_type"],
    url,
    ...(boundedString(candidate.title, 300) ? { title: boundedString(candidate.title, 300) } : {}),
    ...(boundedString(candidate.description, 6000)
      ? { description: boundedString(candidate.description, 6000) }
      : {}),
    ...(safeCover ? { cover_url: safeCover } : {}),
    ...(boundedString(candidate.author_id, 128)
      ? { author_id: boundedString(candidate.author_id, 128) }
      : {}),
    ...(boundedString(candidate.author_name, 128)
      ? { author_name: boundedString(candidate.author_name, 128) }
      : {}),
    ...(boundedString(candidate.published_at, 64)
      ? { published_at: boundedString(candidate.published_at, 64) }
      : {}),
  };
}

function sanitizedEnvelope(value: unknown): InstagramObservedEnvelope | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<InstagramObservedEnvelope>;
  if (!Array.isArray(candidate.items)) return null;
  const route = candidate.route === "topic" || candidate.route === "creator"
    ? candidate.route
    : "unknown";
  const rawItems = candidate.items.slice(0, MAX_ITEMS_PER_ENVELOPE);
  const items = rawItems
    .map(safeObservedItem)
    .filter((item): item is InstagramWireItem => Boolean(item));
  const bridgeRejected = rawItems.length - items.length;
  const boundedCount = (value: unknown): number | undefined => {
    if (!Number.isInteger(value) || Number(value) < 0) return undefined;
    return Math.min(MAX_ITEMS_PER_ENVELOPE, Number(value));
  };
  const observedCount = boundedCount(candidate.observed_count);
  const rejectedCount = boundedCount(candidate.rejected_count);
  return {
    route,
    ...(typeof candidate.collection_id === "string" && candidate.collection_id.length <= 256
      ? { collection_id: candidate.collection_id }
      : {}),
    items,
    ...(observedCount !== undefined ? { observed_count: observedCount } : {}),
    ...({ rejected_count: Math.min(
      MAX_ITEMS_PER_ENVELOPE,
      (rejectedCount ?? 0) + bridgeRejected,
    ) }),
    ...({ shape_valid: candidate.shape_valid !== false && bridgeRejected === 0 }),
    ...(typeof candidate.cursor === "string" && candidate.cursor.length <= 1024
      ? { cursor: candidate.cursor }
      : {}),
    ...(typeof candidate.has_next_page === "boolean"
      ? { has_next_page: candidate.has_next_page }
      : {}),
    ...(candidate.affirmative_terminal === true ? { affirmative_terminal: true } : {}),
  };
}

function receive(event: Event): void {
  const detail = (event as CustomEvent<unknown>).detail;
  let decoded: unknown = detail;
  if (typeof detail === "string") {
    if (new TextEncoder().encode(detail).byteLength > MAX_EVENT_BYTES) return;
    try {
      decoded = JSON.parse(detail) as unknown;
    } catch {
      return;
    }
  }
  const envelope = sanitizedEnvelope(decoded);
  if (!envelope) return;
  buffer.push(envelope);
  if (buffer.length > MAX_ENVELOPES) buffer.splice(0, buffer.length - MAX_ENVELOPES);
}

// This listener is installed synchronously at document_start, before the
// MAIN-world tap can observe Instagram's earliest GraphQL response.
if (typeof window !== "undefined") {
  window.addEventListener(INSTAGRAM_RESPONSE_EVENT, receive);
}

export function requestInstagramResponseReplay(): void {
  window.dispatchEvent(new CustomEvent(INSTAGRAM_REPLAY_EVENT));
}

export function readInstagramResponseBuffer(): InstagramObservedEnvelope[] {
  return buffer.map((entry) => ({ ...entry, items: [...entry.items] }));
}

export function clearInstagramResponseBuffer(): void {
  buffer.length = 0;
}
