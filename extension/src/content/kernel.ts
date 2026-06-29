/**
 * Platform-agnostic collector kernel.
 *
 * Wires generic DOM observers (click / scroll / hover / search /
 * navigation / video) to a PlatformAdapter that supplies selectors,
 * page-type rules, and content-id extraction. Each platform's content
 * script calls `startCollector(adapter)` from its entry file.
 */

import {
  buildActionHintFromClickTarget,
  createBehaviorEvent,
  isTrackableCardElement,
  normalizeActionSignal,
} from "../shared/behavior.js";
import type { BehaviorEvent, PlatformAdapter } from "../shared/types.js";
import { VideoDwellTracker } from "./video-dwell-tracker.js";

const HOVER_DELAY_MS = 800;
const SCROLL_DEBOUNCE_MS = 600;
const HOVER_THROTTLE_MS = 200;

/** Event types that carry a DOM snapshot (navigation + strong signals). */
const SNAPSHOT_TYPES = new Set(["snapshot", "view", "like", "coin", "favorite", "comment"]);

function sendEvent(event: BehaviorEvent): void {
  chrome.runtime.sendMessage({ action: "BEHAVIOR_EVENT", data: event });
}

function closestHref(element: Element): string | null {
  const link = element.closest("a") as (Element & { href?: unknown }) | null;
  if (!link) return null;
  return typeof link.href === "string" ? link.href : link.getAttribute("href");
}

export function startCollector(adapter: PlatformAdapter): void {
  let currentUrl = window.location.href;
  let scrollTimer: number | null = null;
  let lastScrollEventAt = 0;
  let lastHoverCheckAt = 0;
  const hoverTimers = new WeakMap<Element, number>();
  const trackedVideos = new WeakSet<HTMLVideoElement>();

  // v0.3.x event-satisfaction signal: track video-page dwell so the
  // backend can tell meaningful_dwell vs quick_exit on every visit.
  // The kernel only knows when the URL changes; it asks the adapter
  // whether a URL is a video page and reads <video>.duration when
  // available, then hands the lifecycle off to the pure tracker.
  const dwellTracker = new VideoDwellTracker({
    now: () => performance.now(),
    emit: (event) => sendEvent(event),
    buildEvent: (previousUrl, metadata) => ({
      type: "click",
      url: previousUrl,
      title: document.title || "",
      timestamp: Date.now(),
      source_platform: adapter.sourcePlatform,
      context: {
        pageType: adapter.detectPageType(previousUrl),
        viewport: { width: window.innerWidth, height: window.innerHeight },
        scrollPosition: window.scrollY,
      },
      metadata: {
        ...adapter.buildEventMetadata(previousUrl),
        ...metadata,
      },
    }),
  });

  const isVideoPage = (url: string): boolean =>
    adapter.detectPageType(url) === "video";

  const readVideoDuration = (): number | null => {
    const selector = adapter.videoSelector;
    if (!selector) return null;
    const video = document.querySelector(selector);
    if (!(video instanceof HTMLVideoElement)) return null;
    return Number.isFinite(video.duration) ? Number(video.duration.toFixed(2)) : null;
  };

  const enterDwellIfVideoPage = (url: string): void => {
    if (!isVideoPage(url)) return;
    dwellTracker.enter(url, readVideoDuration());
  };

  const createEvent = (
    type: string,
    metadata: Record<string, unknown> = {},
  ): BehaviorEvent =>
    createBehaviorEvent(type, window, document, adapter, metadata, {
      snapshot: SNAPSHOT_TYPES.has(type),
    });

  const buildTargetMetadata = (target: Element): Record<string, unknown> => {
    if (typeof adapter.buildTargetMetadata !== "function") return {};
    try {
      return adapter.buildTargetMetadata(target, window.location.href);
    } catch {
      return {};
    }
  };

  const sendSnapshot = (reason: string): void => {
    sendEvent(createEvent("snapshot", { reason }));
  };

  const observeSearch = (): void => {
    document.addEventListener("keydown", (event) => {
      const target = event.target as HTMLInputElement | null;
      if (!target || event.key !== "Enter") return;
      if (!target.matches(adapter.searchInputSelector)) return;

      const query = target.value?.trim();
      if (!query) return;
      sendEvent(createEvent("search", { query }));
    });
  };

  const observeScroll = (): void => {
    const buildScrollMetadata = (target: EventTarget | null): Record<string, unknown> => {
      if (
        target instanceof HTMLElement &&
        target !== document.body &&
        target !== document.documentElement &&
        target.scrollHeight > target.clientHeight
      ) {
        const maxElementScroll = Math.max(target.scrollHeight - target.clientHeight, 1);
        return {
          scrollRatio: Number((target.scrollTop / maxElementScroll).toFixed(4)),
          scrollY: window.scrollY,
          elementScrollTop: target.scrollTop,
          elementScrollHeight: target.scrollHeight,
          elementClientHeight: target.clientHeight,
          scrollTarget: target.tagName.toLowerCase(),
        };
      }

      const docHeight = Math.max(
        document.body.scrollHeight,
        document.documentElement.scrollHeight,
        1,
      );
      const viewportHeight = window.innerHeight || 1;
      const maxScroll = Math.max(docHeight - viewportHeight, 1);
      return {
        scrollRatio: Number((window.scrollY / maxScroll).toFixed(4)),
        scrollY: window.scrollY,
      };
    };

    const handleScroll = (target: EventTarget | null): void => {
      if (scrollTimer !== null) {
        window.clearTimeout(scrollTimer);
      }
      scrollTimer = window.setTimeout(() => {
        const now = Date.now();
        if (now - lastScrollEventAt < SCROLL_DEBOUNCE_MS) return;
        lastScrollEventAt = now;

        sendEvent(createEvent("scroll", buildScrollMetadata(target)));
      }, SCROLL_DEBOUNCE_MS);
    };

    window.addEventListener(
      "scroll",
      () => handleScroll(window),
      { passive: true },
    );
    document.addEventListener("scroll", (event) => handleScroll(event.target), {
      passive: true,
      capture: true,
    });
  };

  const observeHover = (): void => {
    document.addEventListener("mouseover", (event) => {
      const now = Date.now();
      if (now - lastHoverCheckAt < HOVER_THROTTLE_MS) return;
      lastHoverCheckAt = now;

      const target = event.target as HTMLElement | null;
      const card = target?.closest(adapter.cardSelector);
      if (!card || !isTrackableCardElement(card, adapter)) return;
      if (hoverTimers.has(card)) return;

      const timer = window.setTimeout(() => {
        const anchor =
          card instanceof HTMLAnchorElement
            ? card
            : (card.querySelector("a[href]") as HTMLAnchorElement | null);
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
      const card = target?.closest(adapter.cardSelector);
      if (!card) return;
      const timer = hoverTimers.get(card);
      if (timer !== undefined) {
        window.clearTimeout(timer);
        hoverTimers.delete(card);
      }
    });
  };

  const observeClicks = (): void => {
    document.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) return;
      const target = event.target;
      const href = closestHref(target);
      const targetText = target.textContent?.trim().slice(0, 100) ?? null;
      const targetMetadata = buildTargetMetadata(target);
      sendEvent(
        createEvent("click", {
          ...targetMetadata,
          tagName: target.tagName,
          text: targetText,
          href,
          classList: Array.from(target.classList ?? []),
        }),
      );

      const actionHint = buildActionHintFromClickTarget(target);
      const actionType = adapter.inferActionType(actionHint);

      if (!actionType) return;
      const action = normalizeActionSignal(actionType, {
        ...targetMetadata,
        targetText: actionHint.text?.trim().slice(0, 100) ?? targetText,
        href,
        actionLabel: actionHint.ariaLabel,
      });
      sendEvent(createEvent(action.type, action.metadata));
    }, { capture: true });
  };

  const attachVideoListeners = (): void => {
    const selector = adapter.videoSelector;
    if (!selector) return;

    const video = document.querySelector(selector);
    if (!(video instanceof HTMLVideoElement) || trackedVideos.has(video)) return;

    const buildVideoMetadata = (): Record<string, unknown> => ({
      ...adapter.buildEventMetadata(window.location.href),
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
    // Backfill the dwell tracker once the <video> element has loaded
    // its metadata — many SPAs render the player after the route change.
    video.addEventListener("loadedmetadata", () => {
      if (Number.isFinite(video.duration)) {
        dwellTracker.updateDuration(Number(video.duration.toFixed(2)));
      }
    });

    trackedVideos.add(video);
  };

  const rebindPageObservers = (reason: string): void => {
    attachVideoListeners();
    sendSnapshot(reason);
  };

  const patchHistoryMethod = (methodName: "pushState" | "replaceState"): void => {
    const original = history[methodName];
    history[methodName] = function patched(
      this: History,
      ...args: Parameters<History["pushState"]>
    ): ReturnType<History["pushState"]> {
      const result = original.apply(this, args);
      const nextUrl = window.location.href;
      if (nextUrl !== currentUrl) {
        // Flush dwell BEFORE currentUrl is reassigned so the tracker
        // sees the previous URL — the buildEvent adapter uses that URL
        // to compose the click event.
        dwellTracker.flush(`navigation:${methodName}`);
        currentUrl = nextUrl;
        window.setTimeout(() => {
          rebindPageObservers(`navigation:${methodName}`);
          enterDwellIfVideoPage(nextUrl);
        }, 0);
      }
      return result;
    };
  };

  const observeNavigation = (): void => {
    patchHistoryMethod("pushState");
    patchHistoryMethod("replaceState");
    window.addEventListener("popstate", () => {
      const nextUrl = window.location.href;
      if (nextUrl === currentUrl) return;
      dwellTracker.flush("navigation:popstate");
      currentUrl = nextUrl;
      window.setTimeout(() => {
        rebindPageObservers("navigation:popstate");
        enterDwellIfVideoPage(nextUrl);
      }, 0);
    });
    // Final quick-exit signal when the user closes the tab.
    window.addEventListener("pagehide", () => {
      dwellTracker.flush("pagehide");
    });
  };

  observeClicks();
  observeSearch();
  observeScroll();
  observeHover();
  observeNavigation();
  rebindPageObservers("initial-load");
  enterDwellIfVideoPage(currentUrl);
}
