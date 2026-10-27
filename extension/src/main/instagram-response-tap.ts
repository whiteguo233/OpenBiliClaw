/**
 * MAIN-world, read-only Instagram response tap.
 *
 * Only bounded normalized rows cross into the isolated content script. Raw
 * response bodies, cookies and request headers are never emitted or retained.
 */

import type { InstagramObservedEnvelope } from "../content/instagram/response-buffer.ts";
import { parseInstagramLikedBloks } from "./instagram-liked-bloks.ts";
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

interface UserIdentity {
  id: string;
  keys: { pk?: string; id?: string };
}

interface PublicCreator extends UserIdentity {
  username: string;
}

function strictUserId(value: unknown): string {
  const id = typeof value === "string" ? value.trim()
    : typeof value === "number" && Number.isSafeInteger(value) ? String(value) : "";
  return /^[1-9][0-9]*$/.test(id) ? id : "";
}

function userIdentity(user: Record<string, unknown> | null): UserIdentity | undefined {
  if (!user) return undefined;
  const pk = strictUserId(user.pk);
  const id = strictUserId(user.id);
  // A present invalid primary key must not borrow a valid alternate id.
  if ((user.pk !== undefined && !pk) || (!pk && !id)) return undefined;
  return { id: pk || id, keys: { ...(pk ? { pk } : {}), ...(id ? { id } : {}) } };
}

function sameUserIdentity(left: UserIdentity, right: UserIdentity): boolean {
  if (left.keys.pk && right.keys.pk) return left.keys.pk === right.keys.pk;
  return Boolean(left.keys.id && left.keys.id === right.keys.id);
}

// Only native profile results and the observed ScheduledServerJS -> Relay
// result wrapper may supply privacy evidence. Never walk arbitrary viewer or
// suggested-account objects, or execute the server module instructions.
function creatorProfilesFromPayload(payload: unknown): {
  profiles: Record<string, unknown>[]; truncated: boolean;
} {
  const data = record(record(payload)?.data);
  const profiles = [record(data?.user), record(data?.xig_user_by_username)]
    .filter((value): value is Record<string, unknown> => Boolean(value));
  let remaining = 256;
  let truncated = false;
  const scan = (value: unknown, depth: number): void => {
    const requires = record(value)?.require;
    if (!Array.isArray(requires)) return;
    if (depth > 4) { truncated ||= requires.length > 0; return; }
    if (requires.length > 80) truncated = true;
    for (const entry of requires.slice(0, 80)) {
      if (--remaining < 0 || profiles.length >= 16) { truncated = true; return; }
      if (!Array.isArray(entry) || !Array.isArray(entry[3])) continue;
      const args = entry[3] as unknown[];
      if (entry[0] === "ScheduledServerJS" && entry[1] === "handle") {
        if (args.length > 4) truncated = true;
        for (const arg of args.slice(0, 4)) scan(record(arg)?.__bbox, depth + 1);
      } else if (entry[0] === "RelayPrefetchedStreamCache" && entry[1] === "next") {
        const result = record(record(record(args[1])?.__bbox)?.result);
        const user = record(record(result?.data)?.xig_user_by_username);
        if (user) profiles.push(user);
      }
    }
  };
  scan(payload, 0);
  return { profiles, truncated };
}

function isCreatorPathname(pathname: string): boolean {
  return /^\/[A-Za-z0-9._]+\/?$/.test(pathname)
    && !/^\/(accounts|api|direct|explore|p|popular|reel|reels|stories|your_activity)\/?$/i.test(pathname);
}

function matchesCreatorPath(user: Record<string, unknown>, pathname: string): boolean {
  const username = stringValue(user.username, 128);
  return Boolean(username)
    && pathname.replace(/\/$/, "").toLowerCase() === `/${username.toLowerCase()}`;
}

function publicCreatorFromPayload(payload: unknown, pathname: string): PublicCreator | undefined {
  const evidence = creatorProfilesFromPayload(payload);
  if (evidence.truncated) return undefined;
  const profiles = evidence.profiles.filter(user => matchesCreatorPath(user, pathname));
  if (profiles.some(user => user.is_private !== false)) return undefined;
  const verified = profiles.map(user => {
    // Anonymous Relay profiles expose a numeric GraphQL id distinct from pk.
    // The native profile and media agree on pk (and separately on id); comparing
    // these two namespaces rejects valid public creators. Prefer pk, just as
    // media normalization does. A present invalid pk must not fall back to id.
    const identity = userIdentity(user);
    return identity ? { ...identity, username: stringValue(user.username, 128) } : undefined;
  });
  const first = verified.find(user => user?.keys.pk) || verified[0];
  return first && verified.every(user => user && sameUserIdentity(first, user)) ? first : undefined;
}

let observedPublicCreator: PublicCreator | undefined;

const TOPIC_KEYS = new Set([
  "xdt_fbsearch__top_serp_graphql",
  "xig_logged_out_popular_search_media_info",
  "edge_hashtag_to_media",
  "edge_hashtag_to_top_posts",
]);
const CREATOR_KEYS = new Set([
  "edge_owner_to_timeline_media",
  "xdt_api__v1__feed__user_timeline_graphql_connection",
  "polaris_ordered_timeline_connection",
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

function envelopeFromCollection(collection: LocatedCollection, publicCreator?: PublicCreator): InstagramObservedEnvelope {
  if (collection.key === "xdt_fbsearch__top_serp_graphql") return envelopeFromAuthenticatedTopic(collection);
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
    let canonicalAuthorId: string | undefined;
    if (collection.route === "creator") {
      const edge = record(raw);
      const media = record(edge?.node) || record(edge?.media) || edge;
      const author = record(media?.user) || record(media?.owner);
      const authorIdentity = userIdentity(author);
      const matchingPublicProfile = publicCreator && authorIdentity
        && sameUserIdentity(publicCreator, authorIdentity)
        && stringValue(author?.username, 128).toLowerCase() === publicCreator.username.toLowerCase();
      if (author?.is_private === true || (author?.is_private !== false && !matchingPublicProfile)) {
        rejectedCount += 1;
        continue;
      }
      if (matchingPublicProfile) canonicalAuthorId = publicCreator.keys.pk || authorIdentity.id;
    }
    const item = normalizeMedia(raw);
    if (!item || seen.has(item.id)) {
      rejectedCount += 1;
      continue;
    }
    if (canonicalAuthorId) item.author_id = canonicalAuthorId;
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
  const visibilityUnknown = collection.route === "creator" && !publicCreator && rawItems.length === 0;
  const completeEvidence = rawItems.length <= MAX_ITEMS && rejectedCount === 0 && !visibilityUnknown;
  return {
    route: collection.route,
    collection_id: `${collection.route}:${collection.key}`,
    items,
    observed_count: boundedRawItems.length,
    rejected_count: rejectedCount,
    shape_valid: hasKnownRows && !visibilityUnknown && (boundedRawItems.length === 0 || rejectedCount < boundedRawItems.length),
    ...(cursor ? { cursor } : {}),
    ...(typeof hasNext === "boolean" && completeEvidence ? { has_next_page: hasNext } : {}),
    ...(hasNext === false && completeEvidence ? { affirmative_terminal: true } : {}),
  };
}

function envelopeFromAuthenticatedTopic(collection: LocatedCollection): InstagramObservedEnvelope {
  const rows: unknown[] = [];
  let invalid = !Array.isArray(collection.value.edges);
  let truncated = Array.isArray(collection.value.edges) && collection.value.edges.length > 80;
  for (const edge of (Array.isArray(collection.value.edges) ? collection.value.edges : []).slice(0, 80)) {
    const node = record(record(edge)?.node);
    if (node?.__typename === "XDTTopSerpHeaderUnit" || node?.__typename === "XDTTopSerpAccountsHCMUnit") continue;
    if (node?.__typename !== "XDTTopSerpMediaGridUnit" || !Array.isArray(node.items)) { invalid = true; continue; }
    truncated ||= node.items.length > MAX_ITEMS;
    for (const item of node.items.slice(0, MAX_ITEMS)) {
      // This increment exposes public media, not private-account search results.
      if (record(record(item)?.user)?.is_private !== false) { invalid = true; continue; }
      if (rows.length < MAX_ITEMS) rows.push(item);
      else truncated = true;
    }
  }
  const envelope = envelopeFromCollection({ route: "topic", key: "authenticated_topic_media",
    value: { items: rows, page_info: collection.value.page_info } });
  envelope.collection_id = "topic:xdt_fbsearch__top_serp_graphql";
  envelope.shape_valid = envelope.shape_valid && !invalid;
  if (truncated) {
    delete envelope.affirmative_terminal;
    delete envelope.has_next_page;
  }
  if (invalid) {
    envelope.affirmative_terminal = false;
    envelope.rejected_count = (envelope.rejected_count || 0) + 1;
    envelope.observed_count = (envelope.observed_count || 0) + 1;
  }
  return envelope;
}

/** Decode one upstream payload into bounded normalized envelopes for tests/tap. */
export function parseInstagramObservedPayload(
  payload: unknown, pathname = "", publicCreator?: PublicCreator,
): InstagramObservedEnvelope[] {
  const topicPage = /^\/(?:popular\/[^/]+|explore\/tags\/[^/]+)\/?$/.test(pathname);
  const currentCreator = publicCreatorFromPayload(payload, pathname);
  const evidence = creatorProfilesFromPayload(payload);
  if (!currentCreator && ((evidence.truncated && isCreatorPathname(pathname))
    || evidence.profiles.some(user => matchesCreatorPath(user, pathname)))) {
    return [{ route: "creator", items: [], shape_valid: false, error: "creator_not_public" }];
  }
  const creator = currentCreator || publicCreator;
  return locateCollections(payload)
    .filter(collection => collection.key !== "xdt_fbsearch__top_serp_graphql" || topicPage)
    .map(collection => envelopeFromCollection(collection, creator)).slice(0, 12);
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
  const path = window.location.pathname;
  if (observedPublicCreator && path.replace(/\/$/, "").toLowerCase() !== `/${observedPublicCreator.username.toLowerCase()}`) {
    observedPublicCreator = undefined;
  }
  let current = publicCreatorFromPayload(payload, path);
  const evidence = creatorProfilesFromPayload(payload);
  if (!current && ((evidence.truncated && isCreatorPathname(path))
    || evidence.profiles.some(profile => matchesCreatorPath(profile, path)))) {
    // A fresh matching profile that cannot prove public visibility invalidates
    // old evidence. Only payloads without profile evidence may reuse the cache.
    observedPublicCreator = undefined;
    emit({ route: "creator", items: [], shape_valid: false, error: "creator_not_public" });
    return;
  }
  if (current && observedPublicCreator && sameUserIdentity(current, observedPublicCreator)) {
    // A later id-only profile must not discard a known canonical pk and cause
    // strong/weak SSR profiles to oscillate through recursive DOM scans.
    const keys = { ...observedPublicCreator.keys, ...current.keys };
    current = { ...current, id: keys.pk || current.id, keys };
  }
  const newlyPublic = current && (current.id !== observedPublicCreator?.id
    || current.username !== observedPublicCreator?.username
    || current.keys.id !== observedPublicCreator?.keys.id
    || current.keys.pk !== observedPublicCreator?.keys.pk);
  if (current) observedPublicCreator = current;
  for (const envelope of parseInstagramObservedPayload(payload, path, observedPublicCreator)) {
    // SSR can precede the native profile response. Wait for positive evidence
    // and re-read the existing DOM; never buffer raw/private responses.
    if (envelope.route === "creator" && !observedPublicCreator && !envelope.items.length) continue;
    emit(envelope);
  }
  if (newlyPublic) scanSsrJsonCollections();
}

function isLikedResponse(input: string): boolean {
  try {
    const url = new URL(input, "https://www.instagram.com/");
    return url.hostname === "www.instagram.com" && url.pathname === "/async/wbloks/fetch/"
      && ["liked_media_screen", "liked_refresh", "liked_next"].some(name =>
        url.searchParams.get("appid") === `com.instagram.privacy.activity_center.${name}`);
  } catch { return false; }
}

function inspectTransport(payload: unknown, url: string): void {
  const graph = record(payload);
  if (!isLikedResponse(url) && Array.isArray(graph?.errors) && graph.errors.length
    && record(graph?.data)?.xdt_fbsearch__top_serp_graphql) {
    inspectHttpFailure(500, url);
    return;
  }
  if (isLikedResponse(url)) {
    if (window.location.pathname.replace(/\/$/, "") !== "/your_activity/interactions/likes") return;
    const body = record(payload);
    const message = String(body?.message || "");
    if (body?.status === "fail" || body?.status === "error") {
      const error = /challenge|checkpoint|feedback_required/.test(message) ? "challenge_required"
        : /login_required/.test(message) ? "login_required" : /rate.limit|please.wait/.test(message) ? "rate_limited" : "http_error";
      emit({ route: "liked", items: [], shape_valid: false, error });
      return;
    }
    const envelope = parseInstagramLikedBloks(payload);
    if (envelope) emit(envelope);
    return;
  }
  inspect(payload);
}

function inspectHttpFailure(status: number, url: string): void {
  const path = window.location.pathname.replace(/\/$/, "");
  const profilePath = /^\/[A-Za-z0-9._]+$/.test(path)
    && !/^\/(accounts|api|direct|explore|p|popular|reel|reels|stories|your_activity)$/i.test(path);
  const route = isLikedResponse(url) && path === "/your_activity/interactions/likes" ? "liked"
    : /^\/(popular\/[^/]+|explore\/tags\/[^/]+)$/.test(path) ? "topic"
      : profilePath ? "creator" : null;
  if (!route) return;
  emit({ route, items: [], shape_valid: false,
    error: status === 429 ? "rate_limited" : status === 401 || status === 403 ? "login_required" : "http_error" });
}

function decodeBody(body: string, url: string): unknown {
  return JSON.parse(isLikedResponse(url) ? body.replace(/^for\s*\(;;\);/, "") : body) as unknown;
}

/** Web Relay uses both api/graphql and graphql/query. */
export function isInstagramResponseUrl(input: string): boolean {
  try {
    const url = new URL(input, "https://www.instagram.com/");
    return (url.hostname === "instagram.com" || url.hostname.endsWith(".instagram.com"))
      && (isLikedResponse(input) || url.pathname.replace(/\/$/, "") === "/api/graphql"
        || url.pathname.replace(/\/$/, "") === "/graphql/query"
        || url.pathname.startsWith("/api/v1/"));
  } catch {
    return false;
  }
}

async function inspectResponse(response: Response): Promise<void> {
  if (!isInstagramResponseUrl(response.url)) return;
  if (!response.ok) { inspectHttpFailure(response.status, response.url); return; }
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
    inspectTransport(decodeBody(body, response.url), response.url);
  } catch {
    // Non-JSON/challenge responses are classified by the task executor.
  }
}

let scanningSsr = false;
let ssrRescanRequested = false;

function scanSsrJsonCollections(): void {
  if (typeof document === "undefined" || !document.querySelectorAll) return;
  if (scanningSsr) { ssrRescanRequested = true; return; }
  scanningSsr = true;
  try {
    // A proof after the media carrier needs one follow-up pass. Never recurse
    // or continue indefinitely when multiple native profile shapes coexist.
    for (let pass = 0; pass < 2; pass += 1) {
      ssrRescanRequested = false;
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
      if (!ssrRescanRequested) break;
    }
  } finally {
    scanningSsr = false;
    ssrRescanRequested = false;
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
      if (!isInstagramResponseUrl(target)) return;
      if (this.status < 200 || this.status >= 300) { inspectHttpFailure(this.status, target); return; }
      try {
        if (this.responseType === "json") inspectTransport(this.response, target);
        else if (!this.responseType || this.responseType === "text") {
          const value = this.responseText;
          if (new TextEncoder().encode(value).byteLength <= MAX_RESPONSE_BYTES) {
            inspectTransport(decodeBody(value, target), target);
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
