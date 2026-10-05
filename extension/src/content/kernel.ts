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
  isTapAuthoritativeAction,
  isTrackableCardElement,
  normalizeActionSignal,
} from "../shared/behavior.js";
import type { BehaviorEvent, PlatformAdapter } from "../shared/types.js";
import { isDuplicateSearch, normalizeSearchQuery } from "../shared/platforms/search-query.ts";
import { VideoDwellTracker } from "./video-dwell-tracker.js";

const HOVER_DELAY_MS = 800;
const SCROLL_DEBOUNCE_MS = 600;
const HOVER_THROTTLE_MS = 200;

// Late-rendered <video>: many SPAs insert the player after the route change,
// so a single post-navigation attach misses it. Retry on a bounded timer,
// cancelled on the next navigation.
const _VIDEO_ATTACH_RETRY_MS = 500;
const _VIDEO_ATTACH_MAX_RETRIES = 20;

/** Event types that carry a DOM snapshot (navigation + strong signals). */
const SNAPSHOT_TYPES = new Set(["snapshot", "view", "like", "coin", "favorite", "comment"]);

/** Positive actions whose pressed-state click means a withdrawal (retraction). */
const RETRACTABLE_ACTIONS = new Set(["like", "favorite", "follow"]);

// Bilibili-style dislike controls don't expose aria-pressed, so the DOM path
// cannot tell a fresh dislike from a cancel. Track click parity per control:
// odd clicks are dislikes, even clicks are cancellations emitted as
// retractions (#205). This also caps misfires: N rapid clicks on one control
// can no longer produce N negative votes. State resets after the gap below,
// so a much-later revisit counts as a fresh dislike again.
const DISLIKE_TOGGLE_RESET_MS = 10 * 60_000;
const DISLIKE_TOGGLE_STATE_MAX = 200;

interface DislikeToggleState {
  clicks: number;
  lastAt: number;
}

/** Evidence strength for a retraction — matches the backend's 0.2 default. */
const RETRACTION_SIGNAL_STRENGTH = 0.2;

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
  let videoAttachRetryTimer: number | null = null;
  const hoverTimers = new WeakMap<Element, number>();
  const trackedVideos = new WeakSet<HTMLVideoElement>();
  // Browser <video> elements also fire `seeked` for programmatic seeks
  // (watch-progress restore on page load, Bilibili player switching
  // episodes/parts). Those are not user behavior and must not be recorded
  // as if the user had scrubbed. Only emit `seek` shortly after a real
  // pointer/keyboard interaction, which is how users initiate seeks.
  let lastSeekInputAt = 0;
  const markSeekInput = (): void => {
    lastSeekInputAt = Date.now();
  };

  document.addEventListener("pointerdown", markSeekInput, { capture: true });
  document.addEventListener("pointerup", markSeekInput, { capture: true });
  document.addEventListener("keydown", markSeekInput, { capture: true });

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

  // Last content-page URL we emitted a `view` for — dedups SPA re-renders
  // within a dwell session so a single note/answer/post/status emits once.
  let lastViewedContentUrl: string | null = null;

  const enterDwellIfTrackedPage = (url: string): void => {
    const pageType = adapter.detectPageType(url);
    if (!(adapter.dwellPageTypes ?? ["video"]).includes(pageType)) return;

    if (pageType === "video") {
      // Play-state gated (Phase 3): segments driven by play/pause/bind.
      dwellTracker.enter(url, readVideoDuration(), "playback");
      return;
    }

    // Content page — visibility gated. Begin a segment on entry only when
    // the tab is not hidden; a hidden entry stays segment-closed until the
    // visibilitychange:visible transition (hidden-tab state machine).
    dwellTracker.enter(url, null, "visible");
    if (!document.hidden) {
      dwellTracker.beginSegment();
    }
    // These platforms otherwise emit zero views — synthesise one carrying
    // metadata.content_id from the adapter's extractor (via createEvent).
    if (lastViewedContentUrl !== url) {
      lastViewedContentUrl = url;
      sendEvent(createEvent("view"));
    }
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

  // Shared search-emit guard for both the Enter-key path and the
  // URL-derived path: a nav to a search result page follows the keypress
  // for the same query within a second, so collapse identical normalized
  // queries seen within the dedup window into one `search` event.
  let lastSearch: { query: string; ts: number } | null = null;

  const emitSearch = (query: string): void => {
    const trimmed = query.trim();
    if (!trimmed) return;
    const nowMs = Date.now();
    if (isDuplicateSearch(lastSearch, trimmed, nowMs)) return;
    lastSearch = { query: normalizeSearchQuery(trimmed), ts: nowMs };
    sendEvent(createEvent("search", { query: trimmed }));
  };

  // URL-derived capture: on navigation to a search result page, the query
  // lives in the URL (covers Enter, search-button clicks, suggestion
  // clicks). Null (e.g. X `/explore`) emits nothing.
  const maybeEmitUrlSearch = (url: string): void => {
    if (adapter.detectPageType(url) !== "search") return;
    if (typeof adapter.extractSearchQuery !== "function") return;
    const query = adapter.extractSearchQuery(url);
    if (query) emitSearch(query);
  };

  const observeSearch = (): void => {
    document.addEventListener("keydown", (event) => {
      const target = event.target as HTMLInputElement | null;
      if (!target || event.key !== "Enter") return;
      if (!target.matches(adapter.searchInputSelector)) return;

      const query = target.value?.trim();
      if (!query) return;
      emitSearch(query);
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
    const dislikeToggles = new Map<string, DislikeToggleState>();
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

      // Clicking an already-active like/favorite/follow control withdraws it.
      // A retraction is a neutralization, not a positive vote.
      if (actionHint.pressed === true && RETRACTABLE_ACTIONS.has(actionType)) {
        if (isTapAuthoritativeAction(adapter, "retraction")) {
          // The MAIN-world tap emits the authoritative retraction; suppress
          // the DOM duplicate (no event).
          return;
        }
        sendEvent(
          createEvent("feedback", {
            ...targetMetadata,
            feedback_type: "retraction",
            retracted_action: actionType,
            signal_strength: RETRACTION_SIGNAL_STRENGTH,
            targetText: actionHint.text?.trim().slice(0, 100) ?? targetText,
            href,
            actionLabel: actionHint.ariaLabel,
          }),
        );
        return;
      }

      // Positive strong signal. When the platform's MAIN-world tap is the
      // authoritative source for this action, suppress the DOM emission so we
      // neither double-count the tap nor record "opened the menu = an event"
      // false actions (e.g. clicking Reply/Repost only opens a composer).
      if (isTapAuthoritativeAction(adapter, actionType)) return;

      const action = normalizeActionSignal(actionType, {
        ...targetMetadata,
        targetText: actionHint.text?.trim().slice(0, 100) ?? targetText,
        href,
        actionLabel: actionHint.ariaLabel,
      });
      // Dislike is the one strong signal with no tap-authoritative source and
      // no pressed state on Bilibili, so parity-tracking here is the only way
      // to distinguish "disliked" from "cancelled the dislike" (see comment
      // at DISLIKE_TOGGLE_RESET_MS).
      if (action.type === "feedback" && action.metadata.feedback_type === "dislike") {
        const key = `${window.location.href.split("#")[0]}|${
          (actionHint.text ?? "").trim().slice(0, 100)
        }|${href ?? ""}`;
        const state = dislikeToggles.get(key) ?? { clicks: 0, lastAt: 0 };
        if (Date.now() - state.lastAt > DISLIKE_TOGGLE_RESET_MS) state.clicks = 0;
        state.clicks += 1;
        state.lastAt = Date.now();
        dislikeToggles.delete(key); // refresh insertion order for pruning
        dislikeToggles.set(key, state);
        if (dislikeToggles.size > DISLIKE_TOGGLE_STATE_MAX) {
          const oldest = dislikeToggles.keys().next().value;
          if (oldest !== undefined && oldest !== key) dislikeToggles.delete(oldest);
        }
        if (state.clicks % 2 === 0) {
          // Toggle off: a cancellation, not another negative vote.
          sendEvent(
            createEvent("feedback", {
              ...action.metadata,
              feedback_type: "retraction",
              retracted_action: actionType,
              signal_strength: RETRACTION_SIGNAL_STRENGTH,
            }),
          );
          return;
        }
        sendEvent(createEvent(action.type, action.metadata));
        return;
      }
      sendEvent(createEvent(action.type, action.metadata));
    }, { capture: true });
  };

  // Returns true when a <video> matching the selector is present (freshly
  // attached OR already tracked), false when none exists yet.
  const attachVideoListeners = (): boolean => {
    const selector = adapter.videoSelector;
    if (!selector) return false;

    const video = document.querySelector(selector);
    if (!(video instanceof HTMLVideoElement)) return false;
    if (trackedVideos.has(video)) return true;

    const buildVideoMetadata = (): Record<string, unknown> => ({
      ...adapter.buildEventMetadata(window.location.href),
      currentTime: Number(video.currentTime.toFixed(2)),
      duration: Number.isFinite(video.duration) ? Number(video.duration.toFixed(2)) : null,
    });

    let seekStartTime = video.currentTime;

    // Drive the segmented dwell tracker off play-state so watch_seconds
    // counts only time the video was actually playing.
    video.addEventListener("play", () => {
      dwellTracker.beginSegment();
      sendEvent(createEvent("view", buildVideoMetadata()));
    });
    video.addEventListener("pause", () => {
      dwellTracker.endSegment();
      sendEvent(createEvent("pause", buildVideoMetadata()));
    });
    video.addEventListener("ended", () => {
      dwellTracker.endSegment();
    });
    video.addEventListener("seeking", () => {
      seekStartTime = video.currentTime;
    });
    video.addEventListener("seeked", () => {
      // Programmatic seeks (watch-progress restore, episode/part switch)
      // fire `seeked` without any preceding user pointer/keyboard input.
      // Those are playback mechanics, not the user scrubbing, and would
      // otherwise be recorded as "每隔 N 秒 seek" behavior.
      if (lastSeekInputAt === 0 || Date.now() - lastSeekInputAt > 1500) return;
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

    // Autoplay never fires `play`; if the element is already playing at
    // bind time, begin a segment immediately.
    if (!video.paused && !video.ended) {
      dwellTracker.beginSegment();
    }

    trackedVideos.add(video);
    return true;
  };

  const cancelVideoAttachRetry = (): void => {
    if (videoAttachRetryTimer !== null) {
      window.clearTimeout(videoAttachRetryTimer);
      videoAttachRetryTimer = null;
    }
  };

  const scheduleVideoAttachRetry = (url: string, attempt: number): void => {
    if (attempt > _VIDEO_ATTACH_MAX_RETRIES) return;
    videoAttachRetryTimer = window.setTimeout(() => {
      videoAttachRetryTimer = null;
      // Navigated away since scheduling — abandon this retry chain.
      if (window.location.href !== url || !isVideoPage(url)) return;
      if (attachVideoListeners()) return;
      scheduleVideoAttachRetry(url, attempt + 1);
    }, _VIDEO_ATTACH_RETRY_MS);
  };

  const rebindPageObservers = (reason: string): void => {
    cancelVideoAttachRetry();
    // A stale gesture from a navigation click (e.g. "next episode") must
    // not turn the next video's programmatic seek into a user scrub signal.
    lastSeekInputAt = 0;
    const attached = attachVideoListeners();
    const url = window.location.href;
    if (!attached && isVideoPage(url)) {
      scheduleVideoAttachRetry(url, 1);
    }
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
        cancelVideoAttachRetry();
        currentUrl = nextUrl;
        window.setTimeout(() => {
          // Enter dwell before rebinding so a bind-time (autoplay) segment
          // begin lands on the freshly-created session.
          enterDwellIfTrackedPage(nextUrl);
          maybeEmitUrlSearch(nextUrl);
          rebindPageObservers(`navigation:${methodName}`);
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
      cancelVideoAttachRetry();
      currentUrl = nextUrl;
      window.setTimeout(() => {
        enterDwellIfTrackedPage(nextUrl);
        maybeEmitUrlSearch(nextUrl);
        rebindPageObservers("navigation:popstate");
      }, 0);
    });
    // Final quick-exit signal when the user closes the tab.
    window.addEventListener("pagehide", () => {
      dwellTracker.flush("pagehide");
    });
  };

  const observeVisibility = (): void => {
    // Content-page (visible-mode) dwell is gated by tab visibility; the
    // tracker ignores this for playback sessions.
    document.addEventListener("visibilitychange", () => {
      dwellTracker.handleVisibilityChange(document.hidden);
    });
  };

  observeClicks();
  observeSearch();
  observeScroll();
  observeHover();
  observeNavigation();
  observeVisibility();
  enterDwellIfTrackedPage(currentUrl);
  maybeEmitUrlSearch(currentUrl);
  rebindPageObservers("initial-load");
}
