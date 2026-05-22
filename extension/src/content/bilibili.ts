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
 * Uses a no-cors fetch with a 3-second timeout.
 */
async function checkYouTubeReachable(): Promise<boolean> {
  try {
    await fetch("https://www.youtube.com", {
      mode: "no-cors",
      signal: AbortSignal.timeout(3000),
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * Fetch the backend repost-lookup endpoint for a given BVID.
 * Returns { repost, yt_url } or null on failure.
 */
async function lookupRepost(bvid: string): Promise<{ repost: boolean; yt_url?: string } | null> {
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

  // Try to reach YouTube first
  const youtubeReachable = await checkYouTubeReachable();

  // Fetch repost info from backend
  const result = await lookupRepost(bvid);
  if (!result || !result.repost || !result.yt_url) return;

  // Check if auto-redirect is enabled in config
  let autoRedirect = false;
  try {
    const configResp = await fetch("/api/config", { signal: AbortSignal.timeout(3000) });
    if (configResp.ok) {
      const cfg = await configResp.json();
      autoRedirect = cfg.sources?.youtube?.auto_redirect_youtube === true;
    }
  } catch {
    // Auto-redirect disabled on error
  }

  // If YouTube is reachable and auto-redirect is on, redirect immediately
  if (youtubeReachable && autoRedirect) {
    // Short delay so the user sees the banner for a moment
    setTimeout(() => {
      window.location.href = result.yt_url!;
    }, 500);
    // Still show a brief banner
    showRepostBanner(result.yt_url, false, 0);
    return;
  }

  // Show the banner with a [跳转] button (and optional countdown if YouTube unreachable but auto-redirect wanted)
  showRepostBanner(result.yt_url, autoRedirect && youtubeReachable, 3);
}

// Run repost check on video page load
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    void checkForRepost();
  });
} else {
  void checkForRepost();
}
