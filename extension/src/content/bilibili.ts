/**
 * OpenBiliClaw — Bilibili content script entry.
 *
 * Injected into bilibili.com pages. Wires the generic collector
 * kernel to the bilibili-specific platform adapter, plus checks
 * for YouTube reposts on video pages.
 */

import { startCollector } from "./kernel.js";
import { bilibiliAdapter, extractBvid } from "../shared/platforms/bilibili.js";

startCollector(bilibiliAdapter);

console.log(
  "[OpenBiliClaw] Bilibili behavior collector initialized on",
  bilibiliAdapter.detectPageType(window.location.href),
  "page",
);

// ── YouTube repost redirect ─────────────────────────────────────────

/**
 * Check whether YouTube is reachable from the client.
 *
 * We probe by loading a 1×1 image from a YouTube origin (the favicon
 * is a stable target). This is much cheaper than a no-cors fetch:
 *
 *   - `fetch(..., { mode: "no-cors" })` still performs a full request
 *     round-trip and triggers preflight handling on some browsers,
 *     even though the response is opaque to JS.
 *   - An <img> load is a single GET that the browser cache can
 *     short-circuit (the YouTube favicon is almost certainly cached
 *     already if the user has ever visited the site), and "did the
 *     onload fire?" is exactly the signal we want.
 *
 * Cache-busted with a per-call query param so a cached error doesn't
 * lock us into "unreachable" between probes. 2s timeout.
 *
 * Result is cached for 30s — separate from the auto-redirect cache
 * because reachability is volatile but the setting is stable.
 */
let _reachableCache: { value: boolean; expires: number } | null = null;

async function checkYouTubeReachable(): Promise<boolean> {
  const now = Date.now();
  if (_reachableCache && _reachableCache.expires > now) {
    return _reachableCache.value;
  }
  const result = await new Promise<boolean>((resolve) => {
    const img = new Image();
    const cleanup = () => {
      img.onload = null;
      img.onerror = null;
    };
    const timer = setTimeout(() => {
      cleanup();
      resolve(false);
    }, 2000);
    img.onload = () => {
      clearTimeout(timer);
      cleanup();
      resolve(true);
    };
    img.onerror = () => {
      clearTimeout(timer);
      cleanup();
      resolve(false);
    };
    // Cache-bust so a previous network-error response doesn't pin us.
    img.src = `https://www.youtube.com/favicon.ico?_obc=${now}`;
  });
  _reachableCache = { value: result, expires: now + 30_000 };
  return result;
}

/**
 * Fetch the backend repost-lookup endpoint for a given BVID.
 * Returns the parsed response or null on network/parse failure.
 *
 * The server may answer with `pending=true` when it detected a repost
 * but couldn't talk to YouTube to find the URL; the caller should
 * treat that as "soft yes" and avoid caching.
 */
interface RepostLookup {
  repost: boolean;
  yt_url?: string;
  yt_title?: string;
  yt_uploader?: string;
  pending?: boolean;
}

async function lookupRepost(bvid: string): Promise<RepostLookup | null> {
  try {
    const resp = await fetch(`/api/yt-replacer/lookup?bvid=${encodeURIComponent(bvid)}`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!resp.ok) return null;
    return await resp.json();
  } catch {
    return null;
  }
}

/**
 * Cached snapshot of the global auto-redirect setting. Cached for 60s
 * so we don't re-fetch /api/config on every SPA navigation within the
 * same video-browsing session.
 */
let _autoRedirectCache: { value: boolean; expires: number } | null = null;

async function getAutoRedirectEnabled(): Promise<boolean> {
  const now = Date.now();
  if (_autoRedirectCache && _autoRedirectCache.expires > now) {
    return _autoRedirectCache.value;
  }
  let value = false;
  try {
    const resp = await fetch("/api/config", { signal: AbortSignal.timeout(3000) });
    if (resp.ok) {
      const cfg = await resp.json();
      value = cfg.sources?.youtube?.auto_redirect_youtube === true;
    }
  } catch {
    // Leave value = false on error
  }
  _autoRedirectCache = { value, expires: now + 60_000 };
  return value;
}

/**
 * Create and show a floating banner at the top of the B站 page.
 * The banner offers a [跳转] button and a [关闭] button.
 * If autoRedirect is true, it auto-redirects after countdown seconds.
 */
function showRepostBanner(ytUrl: string, autoRedirect: boolean, countdown: number = 3): void {
  // Avoid duplicate banners
  const existing = document.getElementById("obc-repost-banner");
  if (existing) existing.remove();

  const banner = document.createElement("div");
  banner.id = "obc-repost-banner";
  banner.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 999999;
    background: linear-gradient(135deg, #fb7299, #f65788);
    color: #fff;
    padding: 12px 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 14px;
    font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 14px;
    box-shadow: 0 4px 20px rgba(251, 114, 153, 0.3);
    transform: translateY(-100%);
    transition: transform 0.3s ease;
  `;

  const textSpan = document.createElement("span");
  textSpan.id = "obc-banner-text";
  textSpan.textContent = "此视频是搬运内容，点击跳转YouTube原版";

  const jumpBtn = document.createElement("button");
  jumpBtn.textContent = "跳转";
  jumpBtn.style.cssText = `
    padding: 6px 16px;
    border: 2px solid #fff;
    border-radius: 999px;
    background: transparent;
    color: #fff;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
    transition: background 0.15s, color 0.15s;
  `;
  jumpBtn.addEventListener("mouseenter", () => {
    jumpBtn.style.background = "#fff";
    jumpBtn.style.color = "#fb7299";
  });
  jumpBtn.addEventListener("mouseleave", () => {
    jumpBtn.style.background = "transparent";
    jumpBtn.style.color = "#fff";
  });
  jumpBtn.addEventListener("click", () => {
    window.location.href = ytUrl;
  });

  const closeBtn = document.createElement("button");
  closeBtn.textContent = "关闭";
  closeBtn.style.cssText = `
    padding: 6px 12px;
    border: 1px solid rgba(255,255,255,0.5);
    border-radius: 999px;
    background: rgba(255,255,255,0.15);
    color: #fff;
    font-size: 13px;
    cursor: pointer;
    transition: background 0.15s;
  `;
  closeBtn.addEventListener("mouseenter", () => {
    closeBtn.style.background = "rgba(255,255,255,0.3)";
  });
  closeBtn.addEventListener("mouseleave", () => {
    closeBtn.style.background = "rgba(255,255,255,0.15)";
  });
  closeBtn.addEventListener("click", () => {
    banner.remove();
  });

  banner.appendChild(textSpan);
  banner.appendChild(jumpBtn);
  banner.appendChild(closeBtn);
  document.body.prepend(banner);

  // Slide in
  requestAnimationFrame(() => {
    banner.style.transform = "translateY(0)";
  });

  // Auto-redirect with countdown
  if (autoRedirect) {
    let remaining = countdown;
    const countdownSpan = document.createElement("span");
    countdownSpan.id = "obc-countdown";
    countdownSpan.textContent = `(${remaining}s)`;
    countdownSpan.style.cssText = `
      font-size: 13px;
      opacity: 0.85;
    `;
    banner.insertBefore(countdownSpan, jumpBtn);

    const timer = setInterval(() => {
      remaining--;
      if (remaining <= 0) {
        clearInterval(timer);
        window.location.href = ytUrl;
      } else {
        countdownSpan.textContent = `(${remaining}s)`;
      }
    }, 1000);
  }
}

/**
 * Show a "pending" variant of the banner: we know it's a repost but
 * don't have the YT URL yet (server couldn't reach YouTube). No jump
 * button — there's nowhere to jump to — but we still let the user
 * know so they understand why nothing happened.
 */
function showPendingBanner(): void {
  const existing = document.getElementById("obc-repost-banner");
  if (existing) existing.remove();

  const banner = document.createElement("div");
  banner.id = "obc-repost-banner";
  banner.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 999999;
    background: linear-gradient(135deg, #b89cc7, #8d6da5);
    color: #fff;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 14px;
    font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 13px;
    box-shadow: 0 4px 20px rgba(141, 109, 165, 0.3);
    transform: translateY(-100%);
    transition: transform 0.3s ease;
  `;
  const text = document.createElement("span");
  text.textContent = "检测到搬运视频，但服务器暂时无法连接 YouTube，稍后再试。";
  const closeBtn = document.createElement("button");
  closeBtn.textContent = "关闭";
  closeBtn.style.cssText = `
    padding: 4px 12px;
    border: 1px solid rgba(255,255,255,0.5);
    border-radius: 999px;
    background: rgba(255,255,255,0.15);
    color: #fff;
    font-size: 12px;
    cursor: pointer;
  `;
  closeBtn.addEventListener("click", () => banner.remove());
  banner.appendChild(text);
  banner.appendChild(closeBtn);
  document.body.prepend(banner);
  requestAnimationFrame(() => { banner.style.transform = "translateY(0)"; });
}

/**
 * Check the current page for a YouTube repost.
 * Called on video pages. Extracts BVID, checks the backend,
 * and shows a banner (with optional auto-redirect) if it's a repost.
 */
async function checkForRepost(): Promise<void> {
  const url = window.location.href;

  // Only run on B站 video pages
  if (!url.includes("/video/")) return;

  const bvid = extractBvid(url);
  if (!bvid) return;

  // Basic online check
  if (!navigator.onLine) return;

  // Fire reachability check and backend lookup in parallel — they're
  // independent. checkYouTubeReachable has a 3s timeout, lookupRepost
  // has 5s; we don't block one on the other.
  const [youtubeReachable, result] = await Promise.all([
    checkYouTubeReachable(),
    lookupRepost(bvid),
  ]);

  if (!result || !result.repost) return;

  // Pending state: server detected a repost but couldn't fetch the URL.
  if (!result.yt_url) {
    if (result.pending) showPendingBanner();
    return;
  }

  const ytUrl = result.yt_url;
  const autoRedirect = await getAutoRedirectEnabled();

  // Decide what to show. Three cases:
  //   1. autoRedirect=true  + youtubeReachable=true  → silent banner,
  //      then jump in 500ms.
  //   2. autoRedirect=true  + youtubeReachable=false → countdown banner
  //      (give the user a chance to abort since we're not sure YT loads).
  //   3. autoRedirect=false                          → static banner with
  //      manual [跳转] button.
  if (autoRedirect && youtubeReachable) {
    showRepostBanner(ytUrl, false, 0);
    setTimeout(() => { window.location.href = ytUrl; }, 500);
    return;
  }
  showRepostBanner(ytUrl, autoRedirect, 3);
}

// ── Lifecycle ──────────────────────────────────────────────────────
//
// Bilibili's video pages are an SPA: clicking a recommendation in the
// right rail swaps the BVID in the URL via pushState/replaceState
// without a page load, so a single DOMContentLoaded check would only
// catch the first video. We re-run checkForRepost on every URL change.

let _lastCheckedUrl = "";
function maybeCheckOnUrlChange(): void {
  const url = window.location.href;
  if (url === _lastCheckedUrl) return;
  _lastCheckedUrl = url;
  // Remove any banner from a previous video before deciding on a new one.
  document.getElementById("obc-repost-banner")?.remove();
  void checkForRepost();
}

function installSpaWatcher(): void {
  // popstate fires on back/forward
  window.addEventListener("popstate", maybeCheckOnUrlChange);
  // Bilibili uses history.pushState/replaceState for in-page navigation.
  // Wrap them so we get notified.
  const origPush = history.pushState.bind(history);
  const origReplace = history.replaceState.bind(history);
  history.pushState = function (...args: Parameters<typeof history.pushState>) {
    const ret = origPush(...args);
    queueMicrotask(maybeCheckOnUrlChange);
    return ret;
  };
  history.replaceState = function (...args: Parameters<typeof history.replaceState>) {
    const ret = origReplace(...args);
    queueMicrotask(maybeCheckOnUrlChange);
    return ret;
  };
}

function bootRepostWatcher(): void {
  _lastCheckedUrl = window.location.href;
  installSpaWatcher();
  void checkForRepost();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bootRepostWatcher);
} else {
  bootRepostWatcher();
}
