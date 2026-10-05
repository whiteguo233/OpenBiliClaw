/**
 * YouTube content-script executor — DOM scraper for bootstrap_profile tasks.
 *
 * Scrapes three YouTube pages to collect user interest signals:
 *   yt_history      → /feed/history        (watch history, weak signal)
 *   yt_subscriptions → /feed/channels       (subscribed channels, strong signal)
 *   yt_likes        → /playlist?list=LL    (liked videos, strong signal)
 *
 * Data extraction strategy: DOM selectors on rendered ytd-* elements after
 * scrolling. No MAIN-world injection needed — YouTube renders all data into
 * the DOM and we read it directly from the ISOLATED world.
 */

export type YtScope = "yt_history" | "yt_subscriptions" | "yt_likes";

export interface YtBootstrapItem {
  scope: YtScope;
  video_id?: string;
  channel_id?: string;
  title: string;
  channel: string;
  url: string;
  cover_url?: string;
}

export interface YtScopeExecuteMessage {
  task_id: string;
  scope: YtScope;
  max_items_per_scope?: number;
  max_scroll_rounds?: number;
}

export interface YtScopeResult {
  task_id: string;
  scope: YtScope;
  items: YtBootstrapItem[];
  scope_count: number;
  status: "ok" | "empty" | "failed";
  error?: string;
  debug?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// URL mapping
// ---------------------------------------------------------------------------

export const YT_SCOPE_URLS: Record<YtScope, string> = {
  yt_history: "https://www.youtube.com/feed/history",
  yt_subscriptions: "https://www.youtube.com/feed/channels",
  yt_likes: "https://www.youtube.com/playlist?list=LL",
};

const KNOWN_SCOPES: readonly YtScope[] = [
  "yt_history",
  "yt_subscriptions",
  "yt_likes",
];

export function isKnownScope(s: string): s is YtScope {
  return KNOWN_SCOPES.includes(s as YtScope);
}

// ---------------------------------------------------------------------------
// Pure DOM extractors (exported for unit tests)
// ---------------------------------------------------------------------------

/**
 * Query `selector` inside `root`, falling back to recursively scanning open
 * shadow roots when the direct light-DOM query misses. YouTube's Lit
 * components (``yt-lockup-view-model`` / ``yt-video-card-renderer``) sometimes
 * keep card content inside an open shadow root, where ``Element.querySelector``
 * returns null and the card would silently be skipped.
 */
export function queryIncludingShadow(
  root: ParentNode,
  selector: string,
): Element | null {
  const direct = root.querySelector(selector);
  if (direct) return direct;
  const rootShadow = (root as HTMLElement).shadowRoot;
  if (rootShadow) {
    const found = queryIncludingShadow(rootShadow, selector);
    if (found) return found;
  }
  const descendants = Array.from(root.querySelectorAll<HTMLElement>("*"));
  for (const el of descendants) {
    const shadow = el.shadowRoot;
    if (shadow) {
      const found = queryIncludingShadow(shadow, selector);
      if (found) return found;
    }
  }
  return null;
}

/**
 * Extract items from a watch-history or liked-videos page.
 *
 * YouTube has been migrating from Polymer (``ytd-*``) to Lit (``yt-*`` /
 * ``yt-lockup-view-model``) web components, and history/search cards moved
 * from ``ytd-video-renderer`` to ``ytd-video-card-renderer`` / ``yt-video-card-renderer``.
 * Current ``yt-lockup-view-model`` cards keep title/channel/thumbnail in light
 * DOM under ``ytLockupMetadataViewModel*`` / ``ytContentMetadataViewModel*``
 * classes (no shadow root), so we match those first, keep the legacy
 * selectors for compatibility, and fall back to any ``/watch`` / ``/shorts``
 * anchor inside the card.
 */
export function extractVideoItems(scope: YtScope): YtBootstrapItem[] {
  const items: YtBootstrapItem[] = [];
  const seen = new Set<string>();

  const renderers = Array.from(
    document.querySelectorAll<HTMLElement>(
      [
        "ytd-video-renderer",
        "ytd-playlist-video-renderer",
        "ytd-rich-item-renderer",
        "ytd-video-card-renderer",
        "yt-video-card-renderer",
        "ytd-reel-item-renderer",
        "yt-lockup-view-model",
      ].join(", "),
    ),
  );

  for (const el of renderers) {
    const anchor = (queryIncludingShadow(
      el,
      "a#thumbnail, a#video-title-link, a[id='thumbnail']",
    ) ??
      queryIncludingShadow(el, 'a[href*="/watch"], a[href*="/shorts/"]')) as
      HTMLAnchorElement | null;
    const href = anchor?.href ?? anchor?.getAttribute("href") ?? "";
    const videoId = extractVideoId(href) || extractShortsId(href);

    const titleEl = (queryIncludingShadow(
      el,
      "a.ytLockupMetadataViewModelTitle",
    ) ??
      queryIncludingShadow(el, "#video-title, #video-title-link") ??
      queryIncludingShadow(el, "yt-formatted-string#video-title") ??
      queryIncludingShadow(el, "#video-title yt-formatted-string")) as HTMLElement | null;
    // New cards sometimes render the title only via aria-label / title
    // attribute (text is lazy-rendered or inside a shadow tree).
    const title =
      (titleEl?.textContent ?? "").trim() ||
      (titleEl?.getAttribute("aria-label") ?? "").trim() ||
      (anchor?.getAttribute("aria-label") ?? "").trim() ||
      (anchor?.title ?? "").trim();

    if (!title && !videoId) continue;

    const channelEl = (queryIncludingShadow(
      el,
      "yt-content-metadata-view-model span.ytAttributedStringHost",
    ) ??
      queryIncludingShadow(el, "#channel-name a, ytd-channel-name a, .ytd-channel-name a") ??
      queryIncludingShadow(el, "#channel-name yt-formatted-string") ??
      queryIncludingShadow(el, "#channel-name")) as HTMLElement | null;
    const channel = (channelEl?.textContent ?? "").trim();

    const thumbImg = (queryIncludingShadow(
      el,
      "yt-thumbnail-view-model img, img.ytCoreImageHost",
    ) ??
      queryIncludingShadow(
        el,
        "img#img, img.yt-thumbnail-view-model-wiz__image, yt-image img",
      )) as HTMLImageElement | null;
    const cover_url = thumbImg?.src ?? "";

    const url = videoId
      ? /\/shorts\//.test(href)
        ? `https://www.youtube.com/shorts/${videoId}`
        : `https://www.youtube.com/watch?v=${videoId}`
      : href || "";

    const key = videoId || title;
    if (!key || seen.has(key)) continue;
    seen.add(key);

    items.push({ scope, video_id: videoId || undefined, title, channel, url, cover_url });
  }

  return items;
}

/**
 * Extract channel items from /feed/channels.
 * Matches old ``ytd-channel-renderer`` / ``ytd-grid-channel-renderer`` and the
 * newer ``ytd-channel-card-renderer`` / ``yt-channel-card-renderer`` cards,
 * falling back to any ``/channel/`` or ``/@`` anchor inside the card.
 */
export function extractChannelItems(scope: YtScope): YtBootstrapItem[] {
  const items: YtBootstrapItem[] = [];
  const seen = new Set<string>();

  const renderers = Array.from(
    document.querySelectorAll<HTMLElement>(
      [
        "ytd-channel-renderer",
        "ytd-grid-channel-renderer",
        "ytd-channel-card-renderer",
        "yt-channel-card-renderer",
      ].join(", "),
    ),
  );

  for (const el of renderers) {
    const nameEl = (queryIncludingShadow(
      el,
      "#channel-title, #channel-name, #name",
    ) ??
      queryIncludingShadow(el, "yt-formatted-string#channel-title")) as HTMLElement | null;
    const title = (nameEl?.textContent ?? "").trim();
    if (!title) continue;

    const linkEl = (queryIncludingShadow(
      el,
      "a#main-link, a#channel-title-link, a.channel-link",
    ) ??
      queryIncludingShadow(el, 'a[href*="/channel/"], a[href*="/@"]')) as
      HTMLAnchorElement | null;
    const href = linkEl?.href ?? linkEl?.getAttribute("href") ?? "";
    const channelId = extractChannelId(href);
    const url = href || (channelId ? `https://www.youtube.com/channel/${channelId}` : "");

    const thumbImg = queryIncludingShadow(
      el,
      "img#img, yt-img-shadow img, yt-image img",
    ) as HTMLImageElement | null;
    const cover_url = thumbImg?.src ?? "";

    const key = channelId || title;
    if (!key || seen.has(key)) continue;
    seen.add(key);

    items.push({ scope, channel_id: channelId || undefined, title, channel: title, url, cover_url });
  }

  return items;
}

// ---------------------------------------------------------------------------
// Scroll helper
// ---------------------------------------------------------------------------

export async function scrollAndWait(rounds: number, waitMs: number): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    window.scrollBy({ top: 3000, behavior: "smooth" });
    await sleep(waitMs);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ---------------------------------------------------------------------------
// URL helpers (exported for tests)
// ---------------------------------------------------------------------------

export function extractVideoId(href: string): string {
  const m = href.match(/[?&]v=([A-Za-z0-9_-]{11})/);
  return m ? m[1] : "";
}

export function extractShortsId(href: string): string {
  const m = href.match(/\/shorts\/([A-Za-z0-9_-]{11})/);
  return m ? m[1] : "";
}

export function extractChannelId(href: string): string {
  const m = href.match(/\/channel\/(UC[A-Za-z0-9_-]+)/);
  return m ? m[1] : "";
}

// ---------------------------------------------------------------------------
// Main executor — called from the chrome.runtime.onMessage listener
// ---------------------------------------------------------------------------

export async function executeYtScope(msg: YtScopeExecuteMessage): Promise<YtScopeResult> {
  const { task_id, scope, max_items_per_scope = 300, max_scroll_rounds = 10 } = msg;

  if (!isKnownScope(scope)) {
    return { task_id, scope: scope as YtScope, items: [], scope_count: 0, status: "failed", error: "unknown_scope" };
  }

  // Wait for the page to settle before extracting.
  await sleep(1500);

  const scrollWaitMs = 1500;
  await scrollAndWait(max_scroll_rounds, scrollWaitMs);

  let items: YtBootstrapItem[];
  if (scope === "yt_subscriptions") {
    items = extractChannelItems(scope);
  } else {
    items = extractVideoItems(scope);
  }

  // Cap to max_items_per_scope, most recently rendered first (DOM order = newest first on history).
  const capped = items.slice(0, max_items_per_scope);

  return {
    task_id,
    scope,
    items: capped,
    scope_count: capped.length,
    status: capped.length > 0 ? "ok" : "empty",
    debug: { rendered_count: items.length, capped_count: capped.length, scroll_rounds: max_scroll_rounds },
  };
}

// ---------------------------------------------------------------------------
// Message listener (installed by youtube.ts entry point)
// ---------------------------------------------------------------------------

export function installYtMessageListener(): void {
  chrome.runtime.onMessage.addListener(
    (
      message: { action?: string; data?: YtScopeExecuteMessage },
      _sender,
      sendResponse,
    ) => {
      if (message.action !== "YT_SCOPE_EXECUTE") return false;
      void executeYtScope(message.data as YtScopeExecuteMessage).then((result) => {
        chrome.runtime.sendMessage({ action: "YT_SCOPE_RESULT", data: result });
        sendResponse({ ok: true });
      });
      return true; // async response
    },
  );
}
