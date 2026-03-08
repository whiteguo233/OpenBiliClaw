/**
 * OpenBiliClaw — Bilibili Behavior Collector (Content Script)
 *
 * Injected into Bilibili pages to capture user interactions:
 * - Clicks, scrolls, hover
 * - Video play/pause/seek
 * - Search queries
 * - Comments, likes, coins
 * - DOM page snapshots for context
 */

import {
  createBehaviorEvent,
  detectPageType,
  extractBvid,
  inferActionType,
  isTrackableCardElement,
} from "../shared/behavior.js";
import type { BehaviorEvent } from "../shared/types.js";

const HOVER_DELAY_MS = 800;
const SCROLL_DEBOUNCE_MS = 600;

let currentUrl = window.location.href;
let scrollTimer: number | null = null;
let lastScrollEventAt = 0;
const hoverTimers = new WeakMap<Element, number>();
const trackedVideos = new WeakSet<HTMLVideoElement>();

function sendEvent(event: BehaviorEvent): void {
  chrome.runtime.sendMessage({ action: "BEHAVIOR_EVENT", data: event });
}

function createEvent(type: string, metadata: Record<string, unknown> = {}): BehaviorEvent {
  return createBehaviorEvent(type, window, document, {
    bvid: extractBvid(window.location.href),
    ...metadata,
  });
}

function sendSnapshot(reason: string): void {
  sendEvent(createEvent("snapshot", { reason }));
}

function observeSearch(): void {
  document.addEventListener("keydown", (event) => {
    const target = event.target as HTMLInputElement | null;
    if (!target || event.key !== "Enter") return;

    const isSearchInput = Boolean(
      target.matches(
        'input[type="search"], .nav-search-input, .search-input-el, input[name="keyword"]',
      ),
    );
    if (!isSearchInput) return;

    const query = target.value?.trim();
    if (!query) return;
    sendEvent(createEvent("search", { query }));
  });
}

function observeScroll(): void {
  window.addEventListener(
    "scroll",
    () => {
      if (scrollTimer !== null) {
        window.clearTimeout(scrollTimer);
      }
      scrollTimer = window.setTimeout(() => {
        const now = Date.now();
        if (now - lastScrollEventAt < SCROLL_DEBOUNCE_MS) return;
        lastScrollEventAt = now;

        const docHeight = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, 1);
        const viewportHeight = window.innerHeight || 1;
        const maxScroll = Math.max(docHeight - viewportHeight, 1);
        sendEvent(
          createEvent("scroll", {
            scrollRatio: Number((window.scrollY / maxScroll).toFixed(4)),
            scrollY: window.scrollY,
          }),
        );
      }, SCROLL_DEBOUNCE_MS);
    },
    { passive: true },
  );
}

function observeHover(): void {
  document.addEventListener("mouseover", (event) => {
    const target = event.target as HTMLElement | null;
    const card = target?.closest('a[href*="/video/BV"], .bili-video-card, .video-page-card, .feed-card');
    if (!card || !isTrackableCardElement(card)) return;
    if (hoverTimers.has(card)) return;

    const timer = window.setTimeout(() => {
      const anchor =
        card instanceof HTMLAnchorElement ? card : (card.querySelector('a[href]') as HTMLAnchorElement | null);
      sendEvent(
        createEvent("hover", {
          href: anchor?.getAttribute("href") ?? null,
          text: card.textContent?.trim().slice(0, 120) ?? null,
        }),
      );
      hoverTimers.delete(card);
    }, HOVER_DELAY_MS);
    hoverTimers.set(card, timer);
  });

  document.addEventListener("mouseout", (event) => {
    const target = event.target as HTMLElement | null;
    const card = target?.closest('a[href*="/video/BV"], .bili-video-card, .video-page-card, .feed-card');
    if (!card) return;
    const timer = hoverTimers.get(card);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      hoverTimers.delete(card);
    }
  });
}

function observeClicks(): void {
  document.addEventListener("click", (event) => {
    const target = event.target as HTMLElement;
    const link = target.closest("a");
    sendEvent(
      createEvent("click", {
        tagName: target.tagName,
        text: target.textContent?.trim().slice(0, 100) ?? null,
        href: link?.href ?? null,
        classList: Array.from(target.classList),
      }),
    );

    const actionType = inferActionType({
      text: target.textContent,
      ariaLabel: target.getAttribute("aria-label"),
      className: target.className,
    });

    if (!actionType) return;
    sendEvent(
      createEvent(actionType, {
        targetText: target.textContent?.trim().slice(0, 100) ?? null,
        href: link?.href ?? null,
        actionLabel: target.getAttribute("aria-label"),
      }),
    );
  });
}

function attachVideoListeners(): void {
  const video = document.querySelector("video");
  if (!(video instanceof HTMLVideoElement) || trackedVideos.has(video)) return;

  const buildVideoMetadata = () => ({
    bvid: extractBvid(window.location.href),
    currentTime: Number(video.currentTime.toFixed(2)),
    duration: Number.isFinite(video.duration) ? Number(video.duration.toFixed(2)) : null,
  });

  let seekStartTime = video.currentTime;

  video.addEventListener("play", () => {
    sendEvent(createEvent("view", buildVideoMetadata()));
  });
  video.addEventListener("pause", () => {
    sendEvent(createEvent("pause", buildVideoMetadata()));
  });
  video.addEventListener("seeking", () => {
    seekStartTime = video.currentTime;
  });
  video.addEventListener("seeked", () => {
    sendEvent(
      createEvent("seek", {
        ...buildVideoMetadata(),
        fromTime: Number(seekStartTime.toFixed(2)),
        toTime: Number(video.currentTime.toFixed(2)),
      }),
    );
  });

  trackedVideos.add(video);
}

function rebindPageObservers(reason: string): void {
  attachVideoListeners();
  sendSnapshot(reason);
}

function patchHistoryMethod(methodName: "pushState" | "replaceState"): void {
  const original = history[methodName];
  history[methodName] = function patched(
    this: History,
    ...args: Parameters<History["pushState"]>
  ): ReturnType<History["pushState"]> {
    const result = original.apply(this, args);
    const nextUrl = window.location.href;
    if (nextUrl !== currentUrl) {
      currentUrl = nextUrl;
      window.setTimeout(() => rebindPageObservers(`navigation:${methodName}`), 0);
    }
    return result;
  };
}

function observeNavigation(): void {
  patchHistoryMethod("pushState");
  patchHistoryMethod("replaceState");
  window.addEventListener("popstate", () => {
    const nextUrl = window.location.href;
    if (nextUrl === currentUrl) return;
    currentUrl = nextUrl;
    window.setTimeout(() => rebindPageObservers("navigation:popstate"), 0);
  });
}

function initializeCollector(): void {
  observeClicks();
  observeSearch();
  observeScroll();
  observeHover();
  observeNavigation();
  rebindPageObservers("initial-load");
}

initializeCollector();

console.log("[OpenBiliClaw] Behavior collector initialized on", detectPageType(window.location.href), "page");
