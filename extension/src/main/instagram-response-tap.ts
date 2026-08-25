/**
 * MAIN-world, read-only Instagram response tap.
 *
 * Only bounded normalized rows cross into the isolated content script. Raw
 * response bodies, cookies and request headers are never emitted or retained.
 */

import type { InstagramObservedEnvelope } from "../content/instagram/response-buffer.ts";
import { isInstagramTaskTabLocation } from "../content/instagram/task-mode.ts";
import {
  instagramCanonicalUrl,
  instagramMediaIdentity,
  INSTAGRAM_REPLAY_EVENT,
  INSTAGRAM_RESPONSE_EVENT,
  type InstagramContentType,
} from "../shared/platforms/instagram.ts";

interface WireItem {
  id: string;
  code?: string;
  content_type: InstagramContentType;
  url: string;
  title?: string;
  description?: string;
  cover_url?: string;
  author_id?: string;
  author_name?: string;
  published_at?: string;
}

const MAX_REPLAY_ENVELOPES = 16;
const MAX_ITEMS = 80;
const MAX_RESPONSE_BYTES = 4 * 1024 * 1024;
const replay: InstagramObservedEnvelope[] = [];

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringValue(value: unknown, limit = 6000): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return typeof value === "string" ? value.trim().slice(0, limit) : "";
}

function firstRecord(value: unknown): Record<string, unknown> | null {
  if (Array.isArray(value)) {
    for (const entry of value) {
      const found = record(entry);
      if (found) return found;
    }
  }
  return record(value);
}

function imageUrl(media: Record<string, unknown>): string {
  const versions = record(media.image_versions2);
  const candidate = firstRecord(versions?.candidates);
  return stringValue(
    media.display_uri || media.thumbnail_url || media.image_url || candidate?.url,
    4096,
  );
}

function publishedAt(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    const date = new Date(value < 10_000_000_000 ? value * 1000 : value);
    return Number.isNaN(date.getTime()) ? "" : date.toISOString();
  }
  if (typeof value === "string" && value.trim()) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : date.toISOString();
  }
  return "";
}

function normalizeMedia(input: unknown): WireItem | null {
  const edge = record(input);
  const wrapped = record(edge?.node) || record(edge?.media) || edge;
  if (!wrapped) return null;
  const id = instagramMediaIdentity(wrapped.pk || wrapped.id || wrapped.media_id);
  const code = stringValue(wrapped.code || wrapped.shortcode, 128);
  if (!id || !code) return null;
  const user = record(wrapped.user) || record(wrapped.owner);
  const caption = record(wrapped.caption);
  const description = stringValue(caption?.text || wrapped.accessibility_caption || wrapped.caption, 6000);
  const mediaType = Number(wrapped.media_type);
  const typename = stringValue(wrapped.__typename, 128).toLowerCase();
  const productType = stringValue(wrapped.product_type, 128).toLowerCase();
  const contentType: InstagramContentType =
    Array.isArray(wrapped.carousel_media) || mediaType === 8 || typename.includes("sidecar")
      ? "carousel"
      : mediaType === 2 || productType.includes("clip") || typename.includes("video")
        ? "reel"
        : "post";
  const timestamp = publishedAt(wrapped.taken_at || wrapped.taken_at_timestamp || wrapped.date);
  const authorId = instagramMediaIdentity(user?.pk || user?.id);
  const authorName = stringValue(user?.username, 128);
  return {
    id,
    code,
    content_type: contentType,
    url: instagramCanonicalUrl(contentType, code),
    ...(description ? { title: description.slice(0, 300), description } : {}),
    ...(imageUrl(wrapped) ? { cover_url: imageUrl(wrapped) } : {}),
    ...(authorId ? { author_id: authorId } : {}),
    ...(authorName ? { author_name: authorName } : {}),
    ...(timestamp ? { published_at: timestamp } : {}),
  };
}

interface LocatedCollection {
  route: InstagramObservedEnvelope["route"];
  key: string;
  value: Record<string, unknown>;
}

const TOPIC_KEYS = new Set([
  "xig_logged_out_popular_search_media_info",
  "edge_hashtag_to_media",
  "edge_hashtag_to_top_posts",
]);
const CREATOR_KEYS = new Set([
  "edge_owner_to_timeline_media",
  "xdt_api__v1__feed__user_timeline_graphql_connection",
]);

function locateCollections(
  value: unknown,
  output: LocatedCollection[] = [],
  depth = 0,
  seen = new Set<object>(),
): LocatedCollection[] {
  if (depth > 24 || !value || typeof value !== "object" || seen.has(value as object)) return output;
  seen.add(value as object);
  if (Array.isArray(value)) {
    for (const entry of value.slice(0, 80)) locateCollections(entry, output, depth + 1, seen);
    return output;
  }
  const object = value as Record<string, unknown>;
  for (const [key, nested] of Object.entries(object)) {
    const nestedRecord = record(nested);
    if (nestedRecord && TOPIC_KEYS.has(key)) {
      output.push({ route: "topic", key, value: nestedRecord });
    }
    if (nestedRecord && CREATOR_KEYS.has(key)) {
      output.push({ route: "creator", key, value: nestedRecord });
    }
    locateCollections(nested, output, depth + 1, seen);
    if (output.length >= 12) break;
  }
  return output;
}

function envelopeFromCollection(collection: LocatedCollection): InstagramObservedEnvelope {
  const pageInfo = record(collection.value.page_info);
  const hasKnownRows = Array.isArray(collection.value.edges) || Array.isArray(collection.value.items);
  const rawItems = Array.isArray(collection.value.edges)
    ? collection.value.edges
    : Array.isArray(collection.value.items)
      ? collection.value.items
      : [];
  const items: WireItem[] = [];
  const seen = new Set<string>();
  const boundedRawItems = rawItems.slice(0, MAX_ITEMS);
  let rejectedCount = 0;
  for (const raw of boundedRawItems) {
    const item = normalizeMedia(raw);
    if (!item || seen.has(item.id)) {
      rejectedCount += 1;
      continue;
    }
    seen.add(item.id);
    items.push(item);
  }
  const hasNext = typeof pageInfo?.has_next_page === "boolean"
    ? pageInfo.has_next_page
    : typeof collection.value.more_available === "boolean"
      ? collection.value.more_available
      : undefined;
  const cursor = stringValue(
    pageInfo?.end_cursor || collection.value.next_max_id || collection.value.max_id,
    1024,
  );
  return {
    route: collection.route,
    collection_id: `${collection.route}:${collection.key}`,
    items,
    observed_count: boundedRawItems.length,
    rejected_count: rejectedCount,
    shape_valid: hasKnownRows && (boundedRawItems.length === 0 || rejectedCount < boundedRawItems.length),
    ...(cursor ? { cursor } : {}),
    ...(typeof hasNext === "boolean" ? { has_next_page: hasNext } : {}),
    ...(hasNext === false ? { affirmative_terminal: true } : {}),
  };
}

/** Decode one upstream payload into bounded normalized envelopes for tests/tap. */
export function parseInstagramObservedPayload(payload: unknown): InstagramObservedEnvelope[] {
  return locateCollections(payload).map(envelopeFromCollection).slice(0, 12);
}

function emit(envelope: InstagramObservedEnvelope): void {
  const bounded = { ...envelope, items: envelope.items.slice(0, MAX_ITEMS) };
  replay.push(bounded);
  if (replay.length > MAX_REPLAY_ENVELOPES) replay.shift();
  window.dispatchEvent(new CustomEvent(INSTAGRAM_RESPONSE_EVENT, {
    detail: JSON.stringify(bounded),
  }));
}

function inspect(payload: unknown): void {
  for (const envelope of parseInstagramObservedPayload(payload)) emit(envelope);
}

function relevantUrl(input: string): boolean {
  try {
    const url = new URL(input, window.location.href);
    return (url.hostname === "instagram.com" || url.hostname.endsWith(".instagram.com"))
      && (url.pathname === "/api/graphql" || url.pathname.startsWith("/api/v1/"));
  } catch {
    return false;
  }
}

async function inspectResponse(response: Response): Promise<void> {
  if (!relevantUrl(response.url)) return;
  const length = Number(response.headers.get("content-length") || 0);
  if (length > MAX_RESPONSE_BYTES) return;
  try {
    const clone = response.clone();
    if (!clone.body) return;
    const reader = clone.body.getReader();
    const decoder = new TextDecoder();
    let total = 0;
    let body = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_RESPONSE_BYTES) {
        await reader.cancel().catch(() => {});
        return;
      }
      body += decoder.decode(value, { stream: true });
    }
    body += decoder.decode();
    inspect(JSON.parse(body) as unknown);
  } catch {
    // Non-JSON/challenge responses are classified by the task executor.
  }
}

function scanSsrJsonCollections(): void {
  if (typeof document === "undefined" || !document.querySelectorAll) return;
  for (const script of Array.from(
    document.querySelectorAll('script[type="application/json"][data-sjs]'),
  )) {
    const text = script.textContent || "";
    if (new TextEncoder().encode(text).byteLength > MAX_RESPONSE_BYTES) continue;
    try {
      inspect(JSON.parse(text) as unknown);
    } catch {
      // Non-JSON or oversized SSR payload; the XHR/fetch tap remains authoritative.
    }
  }
}

if (isInstagramTaskTabLocation()) {
  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", scanSsrJsonCollections, { once: true });
  } else {
    scanSsrJsonCollections();
  }

  window.addEventListener(INSTAGRAM_REPLAY_EVENT, () => {
    for (const envelope of replay) {
      window.dispatchEvent(new CustomEvent(INSTAGRAM_RESPONSE_EVENT, {
        detail: JSON.stringify(envelope),
      }));
    }
  });

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args: Parameters<typeof fetch>): Promise<Response> => {
    const response = await originalFetch(...args);
    void inspectResponse(response);
    return response;
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function patchedOpen(
    method: string,
    url: string | URL,
    async: boolean = true,
    username?: string | null,
    password?: string | null,
  ): void {
    const target = String(url);
    this.addEventListener("load", () => {
      if (!relevantUrl(target)) return;
      try {
        if (this.responseType === "json") inspect(this.response);
        else if (!this.responseType || this.responseType === "text") {
          const value = this.responseText;
          if (new TextEncoder().encode(value).byteLength <= MAX_RESPONSE_BYTES) {
            inspect(JSON.parse(value) as unknown);
          }
        }
      } catch {
        // Ignore malformed or HTML responses; no body crosses worlds.
      }
    }, { once: true });
    const invokeOpen = originalOpen as unknown as (
      this: XMLHttpRequest,
      method: string,
      url: string,
      async: boolean,
      username?: string | null,
      password?: string | null,
    ) => void;
    invokeOpen.call(this, method, target, async, username, password);
  };
}
