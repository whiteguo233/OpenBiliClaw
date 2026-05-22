"use strict";
(() => {
  // src/shared/behavior.ts
  function normalizeText(value) {
    return (value ?? "").trim();
  }
  function normalizeActionSignal(actionType, metadata = {}) {
    if (actionType === "dislike") {
      return {
        type: "feedback",
        metadata: {
          ...metadata,
          feedback_type: "dislike",
          reaction: "thumbs_down"
        }
      };
    }
    return { type: actionType, metadata };
  }
  function createDOMSnapshot(doc) {
    const snapshot = {
      title: doc.title,
      h1: normalizeText(doc.querySelector("h1")?.textContent),
      description: doc.querySelector('meta[name="description"]')?.getAttribute("content")?.trim() ?? null,
      author: normalizeText(
        doc.querySelector(
          ".up-name,.username,.bili-video-card__info--author,.up-info__name,.author-wrapper .username,.author-name"
        )?.textContent
      )
    };
    return JSON.stringify(snapshot);
  }
  function createBehaviorContext(win, doc, adapter, options = {}) {
    return {
      pageType: adapter.detectPageType(win.location.href),
      ...options.snapshot !== false && { domSnapshot: createDOMSnapshot(doc) },
      viewport: { width: win.innerWidth, height: win.innerHeight },
      scrollPosition: win.scrollY
    };
  }
  function createBehaviorEvent(type, win, doc, adapter, metadata = {}, options = {}) {
    const url = win.location.href;
    const contentId = adapter.extractContentId(url);
    const platformMeta = adapter.buildEventMetadata(url);
    return {
      type,
      url,
      title: doc.title,
      timestamp: Date.now(),
      source_platform: adapter.sourcePlatform,
      context: createBehaviorContext(win, doc, adapter, options),
      metadata: {
        ...platformMeta,
        ...contentId ? { content_id: contentId } : {},
        ...metadata
      }
    };
  }
  function isTrackableCardElement(element, adapter) {
    if (!element) return false;
    return Boolean(element.closest(adapter.cardSelector));
  }

  // src/content/video-dwell-tracker.ts
  var VideoDwellTracker = class {
    session = null;
    options;
    constructor(options) {
      this.options = options;
    }
    /**
     * Mark that the user entered a video page. If a prior session was
     * still open (no flush happened between two consecutive enters), it
     * is flushed first so we never silently drop dwell.
     */
    enter(url, videoDurationSeconds = null) {
      if (this.session !== null && this.session.url !== url) {
        this.flush("interrupted");
      }
      this.session = {
        url,
        startedAt: this.options.now(),
        videoDurationSeconds
      };
    }
    /**
     * Update the known video duration mid-session. Useful when the
     * <video> element finishes loading metadata after the user arrived.
     */
    updateDuration(videoDurationSeconds) {
      if (this.session === null) return;
      if (videoDurationSeconds === null) return;
      if (!Number.isFinite(videoDurationSeconds)) return;
      this.session.videoDurationSeconds = videoDurationSeconds;
    }
    /**
     * Flush the in-flight dwell. Called on SPA route change, `pagehide`,
     * or a fresh `enter()` on a different URL. Returns the emitted event
     * (or null when there was no session to flush, or the buildEvent
     * adapter rejected it).
     */
    flush(reason) {
      if (this.session === null) return null;
      const elapsed = (this.options.now() - this.session.startedAt) / 1e3;
      const watchSeconds = Math.max(0, Number(elapsed.toFixed(2)));
      const metadata = {
        watch_seconds: watchSeconds,
        dwell_source: "video_page_exit",
        dwell_reason: reason
      };
      if (this.session.videoDurationSeconds !== null) {
        metadata.video_duration_seconds = this.session.videoDurationSeconds;
      }
      const event = this.options.buildEvent(this.session.url, metadata);
      this.session = null;
      if (event === null) return null;
      this.options.emit(event);
      return event;
    }
    /** True iff a dwell session is currently in flight. */
    hasActiveSession() {
      return this.session !== null;
    }
  };

  // src/content/kernel.ts
  var HOVER_DELAY_MS = 800;
  var SCROLL_DEBOUNCE_MS = 600;
  var HOVER_THROTTLE_MS = 200;
  var SNAPSHOT_TYPES = /* @__PURE__ */ new Set(["snapshot", "view", "like", "coin", "favorite", "comment"]);
  function sendEvent(event) {
    chrome.runtime.sendMessage({ action: "BEHAVIOR_EVENT", data: event });
  }
  function startCollector(adapter) {
    let currentUrl = window.location.href;
    let scrollTimer = null;
    let lastScrollEventAt = 0;
    let lastHoverCheckAt = 0;
    const hoverTimers = /* @__PURE__ */ new WeakMap();
    const trackedVideos = /* @__PURE__ */ new WeakSet();
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
          scrollPosition: window.scrollY
        },
        metadata: {
          ...adapter.buildEventMetadata(previousUrl),
          ...metadata
        }
      })
    });
    const isVideoPage = (url) => adapter.detectPageType(url) === "video";
    const readVideoDuration = () => {
      const selector = adapter.videoSelector;
      if (!selector) return null;
      const video = document.querySelector(selector);
      if (!(video instanceof HTMLVideoElement)) return null;
      return Number.isFinite(video.duration) ? Number(video.duration.toFixed(2)) : null;
    };
    const enterDwellIfVideoPage = (url) => {
      if (!isVideoPage(url)) return;
      dwellTracker.enter(url, readVideoDuration());
    };
    const createEvent = (type, metadata = {}) => createBehaviorEvent(type, window, document, adapter, metadata, {
      snapshot: SNAPSHOT_TYPES.has(type)
    });
    const sendSnapshot = (reason) => {
      sendEvent(createEvent("snapshot", { reason }));
    };
    const observeSearch = () => {
      document.addEventListener("keydown", (event) => {
        const target = event.target;
        if (!target || event.key !== "Enter") return;
        if (!target.matches(adapter.searchInputSelector)) return;
        const query = target.value?.trim();
        if (!query) return;
        sendEvent(createEvent("search", { query }));
      });
    };
    const observeScroll = () => {
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
            const docHeight = Math.max(
              document.body.scrollHeight,
              document.documentElement.scrollHeight,
              1
            );
            const viewportHeight = window.innerHeight || 1;
            const maxScroll = Math.max(docHeight - viewportHeight, 1);
            sendEvent(
              createEvent("scroll", {
                scrollRatio: Number((window.scrollY / maxScroll).toFixed(4)),
                scrollY: window.scrollY
              })
            );
          }, SCROLL_DEBOUNCE_MS);
        },
        { passive: true }
      );
    };
    const observeHover = () => {
      document.addEventListener("mouseover", (event) => {
        const now = Date.now();
        if (now - lastHoverCheckAt < HOVER_THROTTLE_MS) return;
        lastHoverCheckAt = now;
        const target = event.target;
        const card = target?.closest(adapter.cardSelector);
        if (!card || !isTrackableCardElement(card, adapter)) return;
        if (hoverTimers.has(card)) return;
        const timer = window.setTimeout(() => {
          const anchor = card instanceof HTMLAnchorElement ? card : card.querySelector("a[href]");
          sendEvent(
            createEvent("hover", {
              href: anchor?.getAttribute("href") ?? null,
              text: card.textContent?.trim().slice(0, 120) ?? null
            })
          );
          hoverTimers.delete(card);
        }, HOVER_DELAY_MS);
        hoverTimers.set(card, timer);
      });
      document.addEventListener("mouseout", (event) => {
        const target = event.target;
        const card = target?.closest(adapter.cardSelector);
        if (!card) return;
        const timer = hoverTimers.get(card);
        if (timer !== void 0) {
          window.clearTimeout(timer);
          hoverTimers.delete(card);
        }
      });
    };
    const observeClicks = () => {
      document.addEventListener("click", (event) => {
        const target = event.target;
        const link = target.closest("a");
        sendEvent(
          createEvent("click", {
            tagName: target.tagName,
            text: target.textContent?.trim().slice(0, 100) ?? null,
            href: link?.href ?? null,
            classList: Array.from(target.classList)
          })
        );
        const actionType = adapter.inferActionType({
          text: target.textContent,
          ariaLabel: target.getAttribute("aria-label"),
          className: target.className
        });
        if (!actionType) return;
        const action = normalizeActionSignal(actionType, {
          targetText: target.textContent?.trim().slice(0, 100) ?? null,
          href: link?.href ?? null,
          actionLabel: target.getAttribute("aria-label")
        });
        sendEvent(createEvent(action.type, action.metadata));
      });
    };
    const attachVideoListeners = () => {
      const selector = adapter.videoSelector;
      if (!selector) return;
      const video = document.querySelector(selector);
      if (!(video instanceof HTMLVideoElement) || trackedVideos.has(video)) return;
      const buildVideoMetadata = () => ({
        ...adapter.buildEventMetadata(window.location.href),
        currentTime: Number(video.currentTime.toFixed(2)),
        duration: Number.isFinite(video.duration) ? Number(video.duration.toFixed(2)) : null
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
            toTime: Number(video.currentTime.toFixed(2))
          })
        );
      });
      video.addEventListener("loadedmetadata", () => {
        if (Number.isFinite(video.duration)) {
          dwellTracker.updateDuration(Number(video.duration.toFixed(2)));
        }
      });
      trackedVideos.add(video);
    };
    const rebindPageObservers = (reason) => {
      attachVideoListeners();
      sendSnapshot(reason);
    };
    const patchHistoryMethod = (methodName) => {
      const original = history[methodName];
      history[methodName] = function patched(...args) {
        const result = original.apply(this, args);
        const nextUrl = window.location.href;
        if (nextUrl !== currentUrl) {
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
    const observeNavigation = () => {
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

  // src/shared/platforms/bilibili.ts
  var BV_PATTERN = /(BV[0-9A-Za-z]{10})/;
  var CARD_SELECTOR = [
    'a[href*="/video/BV"]',
    ".bili-video-card",
    ".video-page-card",
    ".search-all-list .video-item",
    ".feed-card"
  ].join(",");
  var SEARCH_INPUT_SELECTOR = 'input[type="search"], .nav-search-input, .search-input-el, input[name="keyword"]';
  function detectBilibiliPageType(url) {
    if (url.includes("/video/")) return "video";
    if (url.includes("/search")) return "search";
    if (url.includes("space.bilibili.com") || url.includes("/space/")) return "user";
    if (url.includes("/v/")) return "category";
    return "home";
  }
  function extractBvid(url) {
    return url.match(BV_PATTERN)?.[1] ?? null;
  }
  function normalizeText2(value) {
    return (value ?? "").trim();
  }
  function inferBilibiliActionType(hint) {
    const text = `${normalizeText2(hint.text)} ${normalizeText2(hint.ariaLabel)} ${hint.className}`.toLowerCase();
    if (!text) return null;
    if (text.includes("\u4E0D\u611F\u5174\u8DA3") || text.includes("\u4E0D\u559C\u6B22") || text.includes("\u51CF\u5C11\u6B64\u7C7B\u63A8\u8350") || text.includes("\u51CF\u5C11\u63A8\u8350") || text.includes("dislike")) {
      return "dislike";
    }
    if (text.includes("\u70B9\u8D5E") || text.includes("like")) return "like";
    if (text.includes("\u6295\u5E01") || text.includes("coin")) return "coin";
    if (text.includes("\u6536\u85CF") || text.includes("collect") || text.includes("favorite")) {
      return "favorite";
    }
    if (text.includes("\u8BC4\u8BBA") || text.includes("comment")) return "comment";
    return null;
  }
  var bilibiliAdapter = {
    sourcePlatform: "bilibili",
    detectPageType: detectBilibiliPageType,
    extractContentId: extractBvid,
    cardSelector: CARD_SELECTOR,
    searchInputSelector: SEARCH_INPUT_SELECTOR,
    videoSelector: "video",
    inferActionType: inferBilibiliActionType,
    buildEventMetadata(url) {
      return { bvid: extractBvid(url) };
    }
  };

  // src/content/bilibili.ts
  startCollector(bilibiliAdapter);
  console.log(
    "[OpenBiliClaw] Bilibili behavior collector initialized on",
    bilibiliAdapter.detectPageType(window.location.href),
    "page"
  );
})();
//# sourceMappingURL=bilibili.js.map
