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
 * Extract items from a watch-history or liked-videos page.
 * Selects `ytd-video-renderer` and `ytd-playlist-video-renderer` elements.
 */
export function extractVideoItems(scope: YtScope): YtBootstrapItem[] {
  const items: YtBootstrapItem[] = [];
  const seen = new Set<string>();

  const renderers = Array.from(
    document.querySelectorAll<HTMLElement>(
      "ytd-video-renderer, ytd-playlist-video-renderer, ytd-rich-item-renderer",
    ),
  );

  for (const el of renderers) {
    const anchor = el.querySelector<HTMLAnchorElement>(
      "a#thumbnail, a#video-title-link, a[id='thumbnail']",
    );
    const href = anchor?.href ?? anchor?.getAttribute("href") ?? "";
    const videoId = extractVideoId(href);

    const titleEl =
      el.querySelector<HTMLElement>("#video-title, #video-title-link") ??
      el.querySelector<HTMLElement>("yt-formatted-string#video-title");
    const title = (titleEl?.textContent ?? "").trim();

    if (!title && !videoId) continue;

    const channelEl =
      el.querySelector<HTMLElement>(
        "#channel-name a, ytd-channel-name a, .ytd-channel-name a",
      ) ??
      el.querySelector<HTMLElement>("#channel-name yt-formatted-string");
    const channel = (channelEl?.textContent ?? "").trim();

    const thumbImg = el.querySelector<HTMLImageElement>(
      "img#img, img.yt-thumbnail-view-model-wiz__image",
    );
    const cover_url = thumbImg?.src ?? "";

    const url = videoId
      ? `https://www.youtube.com/watch?v=${videoId}`
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
 * Selects `ytd-channel-renderer` and `ytd-grid-channel-renderer` elements.
 */
export function extractChannelItems(scope: YtScope): YtBootstrapItem[] {
  const items: YtBootstrapItem[] = [];
  const seen = new Set<string>();

  const renderers = Array.from(
    document.querySelectorAll<HTMLElement>(
      "ytd-channel-renderer, ytd-grid-channel-renderer",
    ),
  );

  for (const el of renderers) {
    const nameEl =
      el.querySelector<HTMLElement>("#channel-title, #channel-name, #name") ??
      el.querySelector<HTMLElement>("yt-formatted-string#channel-title");
    const title = (nameEl?.textContent ?? "").trim();
    if (!title) continue;

    const linkEl = el.querySelector<HTMLAnchorElement>(
      "a#main-link, a#channel-title-link, a.channel-link",
    );
    const href = linkEl?.href ?? linkEl?.getAttribute("href") ?? "";
    const channelId = extractChannelId(href);
    const url = href || (channelId ? `https://www.youtube.com/channel/${channelId}` : "");

    const thumbImg = el.querySelector<HTMLImageElement>("img#img, yt-img-shadow img");
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
