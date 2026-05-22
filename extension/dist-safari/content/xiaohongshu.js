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
    let scrollTimer2 = null;
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
          if (scrollTimer2 !== null) {
            window.clearTimeout(scrollTimer2);
          }
          scrollTimer2 = window.setTimeout(() => {
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

  // src/shared/platforms/xiaohongshu.ts
  var NOTE_ID_PATTERN = /\/(?:explore|discovery\/item|search_result)\/([0-9a-f]{24})/i;
  var CARD_SELECTOR = [
    'a[href*="/explore/"]',
    'a[href*="/discovery/item/"]',
    'a[href*="/search_result/"]',
    ".note-item",
    ".feeds-page .note-item"
  ].join(",");
  var SEARCH_INPUT_SELECTOR = 'input[placeholder*="\u641C\u7D22"], input[type="search"], .search-input input';
  function detectXiaohongshuPageType(url) {
    if (url.includes("/search_result")) return "search";
    if (url.includes("/explore/") || url.includes("/discovery/item/")) return "note";
    if (url.includes("/user/profile/")) return "user";
    if (url.includes("/explore")) return "home";
    return "home";
  }
  function extractNoteId(url) {
    const match = url.match(NOTE_ID_PATTERN);
    return match ? match[1] : null;
  }
  function normalizeText2(value) {
    return (value ?? "").trim();
  }
  function inferXiaohongshuActionType(hint) {
    const text = `${normalizeText2(hint.text)} ${normalizeText2(hint.ariaLabel)} ${hint.className}`.toLowerCase();
    if (!text) return null;
    if (text.includes("\u70B9\u8D5E") || text.includes("like")) return "like";
    if (text.includes("\u6536\u85CF") || text.includes("collect") || text.includes("favorite")) {
      return "favorite";
    }
    if (text.includes("\u8BC4\u8BBA") || text.includes("comment")) return "comment";
    return null;
  }
  var xiaohongshuAdapter = {
    sourcePlatform: "xiaohongshu",
    detectPageType: detectXiaohongshuPageType,
    extractContentId: extractNoteId,
    cardSelector: CARD_SELECTOR,
    searchInputSelector: SEARCH_INPUT_SELECTOR,
    videoSelector: null,
    inferActionType: inferXiaohongshuActionType,
    buildEventMetadata(url) {
      return { note_id: extractNoteId(url) };
    }
  };

  // src/content/xhs/passive.ts
  var NOTE_PATH_PATTERNS = [/^\/explore\/[^/?#]+/i, /^\/discovery\/item\/[^/?#]+/i];
  var PRESERVED_QUERY_PARAMS = /* @__PURE__ */ new Set(["xsec_token"]);
  var DEFAULT_TOLERANCE_BELOW_PX = 0;
  var DEFAULT_TOLERANCE_ABOVE_PX = 0;
  function classifyXhsPageType(url) {
    if (url.includes("/search_result")) return "search";
    if (url.includes("/user/profile/")) return "profile";
    if (url.includes("/explore/") || url.includes("/discovery/item/")) return "note";
    if (url.includes("/explore")) return "explore";
    return "other";
  }
  function matchesNotePath(pathname) {
    return NOTE_PATH_PATTERNS.some((pattern) => pattern.test(pathname));
  }
  function extractXhsNoteUrl(href, baseUrl) {
    if (!href || href.startsWith("javascript:") || href.startsWith("mailto:")) {
      return null;
    }
    let parsed;
    try {
      parsed = new URL(href, baseUrl);
    } catch {
      return null;
    }
    if (!matchesNotePath(parsed.pathname)) return null;
    const keptParams = new URLSearchParams();
    parsed.searchParams.forEach((value, key) => {
      if (PRESERVED_QUERY_PARAMS.has(key)) {
        keptParams.set(key, value);
      }
    });
    const query = keptParams.toString();
    return `${parsed.origin}${parsed.pathname}${query ? `?${query}` : ""}`;
  }
  function isWithinViewport(rect, viewport, toleranceAbovePx, toleranceBelowPx) {
    const upperBound = viewport.bottom + toleranceBelowPx;
    const lowerBound = viewport.top - toleranceAbovePx;
    return rect.bottom >= lowerBound && rect.top <= upperBound;
  }
  function collectInViewportNoteUrls(anchors, viewport, options) {
    const toleranceBelow = options.toleranceBelowPx ?? DEFAULT_TOLERANCE_BELOW_PX;
    const toleranceAbove = options.toleranceAbovePx ?? DEFAULT_TOLERANCE_ABOVE_PX;
    const ordered = [];
    const seen = /* @__PURE__ */ new Set();
    for (const anchor of anchors) {
      if (!isWithinViewport(anchor.rect, viewport, toleranceAbove, toleranceBelow)) {
        continue;
      }
      const url = extractXhsNoteUrl(anchor.href, options.baseUrl);
      if (!url || seen.has(url)) continue;
      seen.add(url);
      ordered.push(url);
    }
    return ordered;
  }
  function extractNoteMetadataFromAnchor(anchor, baseUrl) {
    const url = extractXhsNoteUrl(anchor.href, baseUrl);
    if (!url) return null;
    const card = anchor.closest(".note-item, section, [class*='note'], [class*='card']") ?? anchor;
    const titleEl = card.querySelector(
      ".title, .note-title, [class*='title'] span, [class*='title']"
    );
    const title = titleEl?.textContent?.trim() || anchor.title || "";
    if (!title) return null;
    const authorEl = card.querySelector(
      ".author-wrapper .name, .author .name, .user-name, [class*='author'] .name, .nickname"
    );
    const author = authorEl?.textContent?.trim() || "";
    const coverImg = card.querySelector(
      "img.cover, .cover img, img[src*='xhscdn'], img[src*='sns-img'], img"
    );
    const cover_url = coverImg?.getAttribute("src") || coverImg?.getAttribute("data-src") || "";
    return { url, title, author, cover_url };
  }
  function filterSelfAuthoredNotes(notes, selfInfo) {
    if (!selfInfo) return [...notes];
    const nickname = (selfInfo.nickname || "").trim().toLowerCase();
    if (!nickname) return [...notes];
    return notes.filter(
      (note) => (note.author || "").trim().toLowerCase() !== nickname
    );
  }
  function dedupeObservedUrls(urls, seen) {
    const fresh = [];
    for (const url of urls) {
      if (seen.has(url)) continue;
      seen.add(url);
      fresh.push(url);
    }
    return fresh;
  }

  // src/content/xhs/bootstrap.ts
  var DEFAULT_BASE_URL = "https://www.xiaohongshu.com";
  var DEFAULT_MAX_ITEMS_PER_SCOPE = 20;
  var MAX_BOOTSTRAP_SCROLL_ROUNDS = 30;
  var DEFAULT_BOOTSTRAP_SCROLL_WAIT_MS = 1200;
  var MIN_BOOTSTRAP_SCROLL_WAIT_MS = 500;
  var MAX_BOOTSTRAP_SCROLL_WAIT_MS = 5e3;
  var DEFAULT_BOOTSTRAP_STAGNANT_SCROLL_ROUNDS = 5;
  var MIN_BOOTSTRAP_STAGNANT_SCROLL_ROUNDS = 1;
  var MAX_BOOTSTRAP_STAGNANT_SCROLL_ROUNDS = 10;
  var BOOTSTRAP_SCOPES = ["saved", "liked", "xhs_history"];
  var OWN_PROFILE_EXACT_SELECTORS = [
    ".main-container .user .link-wrapper a.link-wrapper[href*='/user/profile/']",
    ".main-container .user a[href*='/user/profile/']",
    "nav .user a[href*='/user/profile/']",
    "aside .user a[href*='/user/profile/']"
  ];
  var ANCHOR_SELECTOR = 'a[href*="/explore/"], a[href*="/discovery/item/"]';
  var SCROLL_CONTAINER_SELECTOR = [
    ".feeds-container",
    ".feeds-page",
    ".feeds-list",
    ".note-list",
    ".notes-container",
    ".waterfall",
    ".masonry",
    "[class*='feeds']",
    "[class*='Feeds']",
    "[class*='waterfall']",
    "[class*='Waterfall']",
    "[class*='masonry']",
    "[class*='Masonry']",
    "[class*='note-list']",
    "[class*='NoteList']",
    "[class*='scroll']",
    "[class*='Scroll']"
  ].join(", ");
  function isRecord(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value);
  }
  function elementClassName(element) {
    const className = element.className;
    if (typeof className === "string") return className.trim();
    if (isRecord(className) && typeof className.baseVal === "string") {
      return className.baseVal.trim();
    }
    return "";
  }
  function describeBootstrapScrollTarget(element) {
    const tag = element.tagName?.toLowerCase?.() || "element";
    const id = element.id ? `#${element.id}` : "";
    const className = elementClassName(element);
    const classes = className ? `.${className.split(/\s+/).filter(Boolean).slice(0, 3).join(".")}` : "";
    return `${tag}${id}${classes}`;
  }
  function readBootstrapScrollMetrics(element) {
    return {
      target: describeBootstrapScrollTarget(element),
      scroll_top: Math.max(0, Math.floor(element.scrollTop || 0)),
      scroll_height: Math.max(0, Math.floor(element.scrollHeight || 0)),
      client_height: Math.max(0, Math.floor(element.clientHeight || 0))
    };
  }
  function countNoteAnchors(element) {
    try {
      return element.querySelectorAll(ANCHOR_SELECTOR).length;
    } catch {
      return 0;
    }
  }
  function readOverflowY(element) {
    const win = element.ownerDocument?.defaultView;
    if (!win?.getComputedStyle) return "unknown";
    return win.getComputedStyle(element).overflowY.toLowerCase();
  }
  function hasScrollableOverflowStyle(element) {
    const overflowY = readOverflowY(element);
    if (overflowY === "unknown") return true;
    return overflowY === "auto" || overflowY === "scroll" || overflowY === "overlay";
  }
  function scrollContainerScore(element) {
    const clientHeight = element.clientHeight || 0;
    if (clientHeight < 120) return 0;
    if (!hasScrollableOverflowStyle(element)) return 0;
    const overflow = Math.max(0, (element.scrollHeight || 0) - (element.clientHeight || 0));
    if (overflow < 120) return 0;
    const descriptor = `${element.id || ""} ${elementClassName(element)}`.toLowerCase();
    if (descriptor.includes("channel-list") || descriptor.includes("side-bar") || descriptor.includes("sidebar")) {
      return 0;
    }
    const keywordScore = descriptor.includes("feed") || descriptor.includes("waterfall") || descriptor.includes("masonry") || descriptor.includes("note") ? 1e3 : 0;
    return overflow + countNoteAnchors(element) * 2e3 + keywordScore;
  }
  function findBootstrapScrollContainer(doc) {
    const candidates = collectBootstrapScrollCandidates(doc, Number.POSITIVE_INFINITY);
    if (candidates.length === 0) return null;
    let bestElement = null;
    let bestScore = 0;
    const elements = bootstrapScrollCandidateElements(doc);
    for (const element of elements) {
      const score = scrollContainerScore(element);
      if (score > bestScore) {
        bestElement = element;
        bestScore = score;
      }
    }
    return bestElement;
  }
  function bootstrapScrollCandidateElements(doc) {
    const seen = /* @__PURE__ */ new Set();
    const candidates = [];
    const addCandidate = (element) => {
      if (seen.has(element)) return;
      seen.add(element);
      candidates.push(element);
    };
    try {
      doc.querySelectorAll(SCROLL_CONTAINER_SELECTOR).forEach(addCandidate);
      doc.querySelectorAll("body *").forEach(addCandidate);
    } catch {
      return [];
    }
    return candidates;
  }
  function collectBootstrapScrollCandidates(doc, limit = 10) {
    return bootstrapScrollCandidateElements(doc).map((element) => {
      const metrics = readBootstrapScrollMetrics(element);
      return {
        ...metrics,
        overflow_y: readOverflowY(element),
        note_count: countNoteAnchors(element),
        score: scrollContainerScore(element)
      };
    }).filter((candidate) => candidate.score > 0).sort((a, b) => b.score - a.score).slice(0, Math.max(0, Math.floor(limit)));
  }
  function unwrapReactive(value) {
    let current = value;
    const seen = /* @__PURE__ */ new Set();
    while (isRecord(current) && !seen.has(current)) {
      seen.add(current);
      if ("_rawValue" in current) {
        current = current._rawValue;
        continue;
      }
      if ("_value" in current) {
        current = current._value;
        continue;
      }
      if ("value" in current && Object.keys(current).length <= 3) {
        current = current.value;
        continue;
      }
      break;
    }
    return current;
  }
  function getPath(value, path) {
    let current = unwrapReactive(value);
    for (const part of path) {
      if (!isRecord(current)) return void 0;
      current = unwrapReactive(current[part]);
    }
    return current;
  }
  function firstString(...values) {
    for (const value of values) {
      const raw = unwrapReactive(value);
      if (typeof raw === "string" && raw.trim()) return raw.trim();
      if (typeof raw === "number") return String(raw);
    }
    return "";
  }
  function firstPathString(value, paths) {
    for (const path of paths) {
      const found = firstString(getPath(value, path));
      if (found) return found;
    }
    return "";
  }
  function flattenNotes(value) {
    const raw = unwrapReactive(value);
    if (Array.isArray(raw)) {
      return raw.flatMap((item) => flattenNotes(item));
    }
    if (!isRecord(raw)) return [];
    for (const key of ["notes", "items", "list", "data"]) {
      const nested = unwrapReactive(raw[key]);
      if (Array.isArray(nested)) return flattenNotes(nested);
    }
    return [raw];
  }
  function notesForScope(state, scope) {
    const userGroups = unwrapReactive(getPath(state, ["user", "notes"]));
    if (Array.isArray(userGroups)) {
      if (scope === "saved") return flattenNotes(userGroups[1]);
      if (scope === "liked") return flattenNotes(userGroups[2]);
    }
    if (scope === "saved") {
      return [
        ...flattenNotes(getPath(state, ["saved", "notes"])),
        ...flattenNotes(getPath(state, ["collect", "notes"])),
        ...flattenNotes(getPath(state, ["collections", "notes"]))
      ];
    }
    if (scope === "liked") {
      return [
        ...flattenNotes(getPath(state, ["liked", "notes"])),
        ...flattenNotes(getPath(state, ["likes", "notes"]))
      ];
    }
    return [
      ...flattenNotes(getPath(state, ["history", "notes"])),
      ...flattenNotes(getPath(state, ["footprint", "notes"])),
      ...flattenNotes(getPath(state, ["browseHistory", "notes"])),
      ...flattenNotes(getPath(state, ["browsingHistory", "notes"]))
    ];
  }
  function noteIdFromUrl(url) {
    if (!url) return "";
    try {
      const parsed = new URL(url, DEFAULT_BASE_URL);
      const parts = parsed.pathname.split("/").filter(Boolean);
      return parts.at(-1) ?? "";
    } catch {
      return "";
    }
  }
  function buildNoteUrl(noteId, xsecToken, baseUrl) {
    const url = new URL(`/explore/${noteId}`, baseUrl || DEFAULT_BASE_URL);
    if (xsecToken) url.searchParams.set("xsec_token", xsecToken);
    return url.toString();
  }
  function normalizeUrl(url, baseUrl) {
    if (!url) return "";
    try {
      const parsed = new URL(url, baseUrl || DEFAULT_BASE_URL);
      if (!parsed.pathname.startsWith("/explore/") && !parsed.pathname.startsWith("/discovery/item/")) {
        return "";
      }
      const xsecToken = parsed.searchParams.get("xsec_token") ?? "";
      const keptParams = new URLSearchParams();
      if (xsecToken) keptParams.set("xsec_token", xsecToken);
      const query = keptParams.toString();
      return `${parsed.origin}${parsed.pathname}${query ? `?${query}` : ""}`;
    } catch {
      return "";
    }
  }
  function normalizeProfileUrl(url, baseUrl) {
    if (!url) return "";
    try {
      const parsed = new URL(url, baseUrl || DEFAULT_BASE_URL);
      if (!parsed.pathname.startsWith("/user/profile/")) return "";
      const keptParams = new URLSearchParams();
      const xsecToken = parsed.searchParams.get("xsec_token") ?? "";
      const xsecSource = parsed.searchParams.get("xsec_source") ?? "";
      if (xsecToken) keptParams.set("xsec_token", xsecToken);
      if (xsecSource) keptParams.set("xsec_source", xsecSource);
      const query = keptParams.toString();
      return `${parsed.origin}${parsed.pathname.replace(/\/$/, "")}${query ? `?${query}` : ""}`;
    } catch {
      return "";
    }
  }
  function anchorHref(anchor) {
    return anchor.getAttribute("href") || anchor.href || "";
  }
  function normalizedAnchorProfileUrl(anchor, baseUrl) {
    return normalizeProfileUrl(anchorHref(anchor), baseUrl);
  }
  function isOwnProfileNavAnchor(anchor) {
    const text = anchor.textContent?.trim() ?? "";
    const aria = anchor.getAttribute("aria-label")?.trim() ?? "";
    const title = anchor.getAttribute("title")?.trim() ?? "";
    const className = String(anchor.className ?? "");
    return text === "\u6211" || aria === "\u6211" || title === "\u6211" || className.includes("link-wrapper") && anchor.closest(".user, nav, aside") !== null;
  }
  function firstBoolean(...values) {
    for (const value of values) {
      const raw = unwrapReactive(value);
      if (typeof raw === "boolean") return raw;
      if (typeof raw === "string") {
        if (raw === "true") return true;
        if (raw === "false") return false;
      }
    }
    return null;
  }
  function extractOwnProfileUrlFromState(state, baseUrl = DEFAULT_BASE_URL) {
    const loggedIn = firstBoolean(getPath(state, ["user", "loggedIn"]));
    if (loggedIn !== true) return "";
    const userId = firstPathString(state, [
      ["user", "userInfo", "userId"],
      ["user", "userInfo", "user_id"],
      ["user", "userInfo", "id"],
      ["user", "userPageData", "basicInfo", "userId"],
      ["user", "userPageData", "basicInfo", "user_id"]
    ]);
    if (!userId) return "";
    return normalizeProfileUrl(`/user/profile/${userId}`, baseUrl);
  }
  function extractSelfInfoFromState(state) {
    const loggedIn = firstBoolean(getPath(state, ["user", "loggedIn"]));
    if (loggedIn !== true) return null;
    const userId = firstPathString(state, [
      ["user", "userInfo", "userId"],
      ["user", "userInfo", "user_id"],
      ["user", "userInfo", "id"],
      ["user", "userPageData", "basicInfo", "userId"],
      ["user", "userPageData", "basicInfo", "user_id"]
    ]);
    const nickname = firstPathString(state, [
      ["user", "userInfo", "nickname"],
      ["user", "userInfo", "nickName"],
      ["user", "userInfo", "nick_name"],
      ["user", "userInfo", "name"],
      ["user", "userPageData", "basicInfo", "nickname"],
      ["user", "userPageData", "basicInfo", "nickName"]
    ]);
    if (!userId && !nickname) return null;
    return { user_id: userId, nickname };
  }
  function extractOwnProfileUrlFromDocument(doc, baseUrl) {
    const anchor = findOwnProfileAnchorFromDocument(doc, baseUrl);
    return anchor ? normalizedAnchorProfileUrl(anchor, baseUrl) : "";
  }
  function findOwnProfileAnchorFromDocument(doc, baseUrl) {
    for (const selector of OWN_PROFILE_EXACT_SELECTORS) {
      const anchor = doc.querySelector(selector);
      const url = anchor ? normalizedAnchorProfileUrl(anchor, baseUrl) : "";
      if (url && anchor) return anchor;
    }
    const anchors = Array.from(doc.querySelectorAll("a[href*='/user/profile/']"));
    for (const anchor of anchors) {
      if (!isOwnProfileNavAnchor(anchor)) continue;
      if (normalizedAnchorProfileUrl(anchor, baseUrl)) return anchor;
    }
    return null;
  }
  function dispatchOwnProfileMouseEvent(anchor, win, type) {
    try {
      const MouseEventCtor = win.MouseEvent ?? (typeof MouseEvent === "function" ? MouseEvent : null);
      if (!MouseEventCtor) throw new Error("MouseEvent unavailable");
      anchor.dispatchEvent(
        new MouseEventCtor(type, { bubbles: true, cancelable: true, view: win })
      );
    } catch {
      try {
        anchor.dispatchEvent(new Event(type, { bubbles: true, cancelable: true }));
      } catch {
      }
    }
  }
  function clickOwnProfileAnchorFromDocument(doc, baseUrl, win) {
    const anchor = findOwnProfileAnchorFromDocument(doc, baseUrl);
    if (!anchor) return { url: "", clicked: false };
    const url = normalizedAnchorProfileUrl(anchor, baseUrl);
    if (!url) return { url: "", clicked: false };
    try {
      anchor.scrollIntoView({ block: "center", inline: "center" });
    } catch {
    }
    dispatchOwnProfileMouseEvent(anchor, win, "mousedown");
    dispatchOwnProfileMouseEvent(anchor, win, "mouseup");
    try {
      anchor.click();
    } catch {
      return { url, clicked: false };
    }
    return { url, clicked: true };
  }
  function normalizeStateNote(rawNote, scope, baseUrl) {
    if (!isRecord(rawNote)) return null;
    const title = firstPathString(rawNote, [
      ["title"],
      ["display_title"],
      ["displayTitle"],
      ["desc"],
      ["name"],
      ["noteCard", "display_title"],
      ["noteCard", "displayTitle"],
      ["noteCard", "title"],
      ["note_card", "display_title"],
      ["note_card", "displayTitle"],
      ["note_card", "title"]
    ]);
    const noteId = firstPathString(rawNote, [
      ["note_id"],
      ["noteId"],
      ["id"],
      ["noteCard", "note_id"],
      ["noteCard", "noteId"],
      ["noteCard", "id"],
      ["note_card", "note_id"],
      ["note_card", "id"]
    ]);
    const xsecToken = firstPathString(rawNote, [
      ["xsec_token"],
      ["xsecToken"],
      ["xsec"],
      ["noteCard", "xsec_token"],
      ["noteCard", "xsecToken"],
      ["note_card", "xsec_token"]
    ]);
    const explicitUrl = normalizeUrl(
      firstPathString(rawNote, [
        ["url"],
        ["link"],
        ["href"],
        ["shareUrl"],
        ["share_url"],
        ["noteCard", "url"],
        ["note_card", "url"]
      ]),
      baseUrl
    );
    const url = explicitUrl || (noteId ? buildNoteUrl(noteId, xsecToken, baseUrl) : "");
    const normalizedNoteId = noteId || noteIdFromUrl(url);
    const author = firstPathString(rawNote, [
      ["author"],
      ["nickname"],
      ["user", "nickname"],
      ["user", "nickName"],
      ["user", "nick_name"],
      ["user", "name"],
      ["user_info", "nickname"],
      ["userInfo", "nickname"],
      ["noteCard", "user", "nickname"],
      ["noteCard", "user", "nickName"],
      ["note_card", "user", "nickname"],
      ["note_card", "user", "nickName"]
    ]);
    const coverUrl = firstPathString(rawNote, [
      ["cover_url"],
      ["coverUrl"],
      ["cover", "url"],
      ["cover", "urlDefault"],
      ["cover", "src"],
      ["image", "url"],
      ["images_list", "0", "url"],
      ["imageList", "0", "url"],
      ["noteCard", "cover", "url"],
      ["noteCard", "cover", "urlDefault"],
      ["note_card", "cover", "url"],
      ["note_card", "cover", "urlDefault"]
    ]);
    if (!title && !url) return null;
    return {
      scope,
      url,
      title,
      author,
      cover_url: coverUrl,
      note_id: normalizedNoteId,
      xsec_token: xsecToken
    };
  }
  function normalizeBootstrapScopes(scopes) {
    if (!scopes?.length) return [...BOOTSTRAP_SCOPES];
    const out = [];
    for (const scope of scopes) {
      if ((scope === "saved" || scope === "liked" || scope === "xhs_history") && !out.includes(scope)) {
        out.push(scope);
      }
    }
    return out.length ? out : [...BOOTSTRAP_SCOPES];
  }
  function extractBootstrapNotesFromState(state, scopes, options = {}) {
    const requestedScopes = normalizeBootstrapScopes(scopes);
    const baseUrl = options.baseUrl ?? DEFAULT_BASE_URL;
    const maxItems = Math.max(1, options.maxItemsPerScope ?? DEFAULT_MAX_ITEMS_PER_SCOPE);
    const notes = [];
    for (const scope of requestedScopes) {
      const seen = /* @__PURE__ */ new Set();
      for (const raw of notesForScope(state, scope)) {
        if (notes.filter((note2) => note2.scope === scope).length >= maxItems) break;
        const note = normalizeStateNote(raw, scope, baseUrl);
        if (!note) continue;
        const key = note.note_id || note.url || note.title;
        if (!key || seen.has(key)) continue;
        seen.add(key);
        notes.push(note);
      }
    }
    return notes;
  }
  function countBootstrapStateNotesByScope(state, scopes, options = {}) {
    const counts = {};
    for (const scope of normalizeBootstrapScopes(scopes)) {
      counts[scope] = extractBootstrapNotesFromState(state, [scope], options).length;
    }
    return counts;
  }
  function buildBootstrapDebugPayload(step) {
    return { xhs_bootstrap: { steps: [step] } };
  }
  function buildBootstrapPartialPayload(input) {
    return {
      task_id: input.taskId,
      status: "partial",
      urls: [...new Set(input.notes.map((note) => note.url).filter(Boolean))],
      notes: input.notes,
      scope_counts: input.scopeCounts,
      debug: {
        xhs_bootstrap_partial: {
          scope: input.scope,
          round: input.round,
          count: input.notes.length
        }
      }
    };
  }
  function bootstrapProfileTabLabels(scope) {
    if (scope === "saved") return ["\u6536\u85CF"];
    if (scope === "liked") return ["\u8D5E\u8FC7", "\u559C\u6B22", "\u70B9\u8D5E"];
    return [];
  }
  function normalizeBootstrapScrollRounds(rounds) {
    if (!Number.isFinite(rounds) || rounds === void 0 || rounds <= 0) return 0;
    return Math.min(Math.floor(rounds), MAX_BOOTSTRAP_SCROLL_ROUNDS);
  }
  function normalizeBootstrapScrollWaitMs(waitMs) {
    if (!Number.isFinite(waitMs) || waitMs === void 0) return DEFAULT_BOOTSTRAP_SCROLL_WAIT_MS;
    return Math.min(
      Math.max(Math.floor(waitMs), MIN_BOOTSTRAP_SCROLL_WAIT_MS),
      MAX_BOOTSTRAP_SCROLL_WAIT_MS
    );
  }
  function normalizeBootstrapStagnantScrollRounds(rounds) {
    if (!Number.isFinite(rounds) || rounds === void 0) {
      return DEFAULT_BOOTSTRAP_STAGNANT_SCROLL_ROUNDS;
    }
    return Math.min(
      Math.max(Math.floor(rounds), MIN_BOOTSTRAP_STAGNANT_SCROLL_ROUNDS),
      MAX_BOOTSTRAP_STAGNANT_SCROLL_ROUNDS
    );
  }
  function bootstrapScrollShouldContinue(decision) {
    if (decision.maxScrollRounds <= 0) return false;
    if (decision.currentCount >= decision.maxItemsPerScope) return false;
    if (decision.round >= decision.maxScrollRounds) return false;
    return decision.stagnantRounds < normalizeBootstrapStagnantScrollRounds(
      decision.maxStagnantScrollRounds
    );
  }
  function extractBootstrapNotesFromDocument(doc, scope, baseUrl, options = {}) {
    const maxItems = Math.max(1, options.maxItemsPerScope ?? DEFAULT_MAX_ITEMS_PER_SCOPE);
    const notes = [];
    const seen = /* @__PURE__ */ new Set();
    const anchors = doc.querySelectorAll(ANCHOR_SELECTOR);
    anchors.forEach((anchor) => {
      if (notes.length >= maxItems) return;
      const url = normalizeUrl(anchor.href, baseUrl);
      if (!url) return;
      const noteId = noteIdFromUrl(url);
      const key = noteId || url;
      if (!key || seen.has(key)) return;
      seen.add(key);
      const card = anchor.closest(".note-item, section, [class*='note'], [class*='card']") ?? anchor;
      const titleEl = card.querySelector(
        ".title, .note-title, [class*='title'] span, [class*='title']"
      );
      const authorEl = card.querySelector(
        ".author-wrapper .name, .author .name, .user-name, [class*='author'] .name, .nickname"
      );
      const coverImg = card.querySelector(
        "img.cover, .cover img, img[src*='xhscdn'], img[src*='sns-img'], img"
      );
      const parsed = new URL(url);
      notes.push({
        scope,
        url,
        title: titleEl?.textContent?.trim() || anchor.title || "",
        author: authorEl?.textContent?.trim() || "",
        cover_url: coverImg?.getAttribute("src") || coverImg?.getAttribute("data-src") || "",
        note_id: noteId,
        xsec_token: parsed.searchParams.get("xsec_token") ?? ""
      });
    });
    return notes.filter((note) => note.title || note.url);
  }
  function extractBootstrapNotesFromProfileDocument(doc, scope, baseUrl, options = {}) {
    if (scope === "xhs_history") return [];
    return extractBootstrapNotesFromDocument(doc, scope, baseUrl, options);
  }
  function hasBootstrapProfileContent(doc) {
    try {
      if (extractBootstrapStateFromDocument(doc) !== null) return true;
    } catch {
    }
    const text = doc.body?.textContent?.replace(/\s+/g, "") ?? "";
    if (text.includes("\u6536\u85CF") || text.includes("\u8D5E\u8FC7") || text.includes("\u559C\u6B22") || text.includes("\u70B9\u8D5E")) {
      return true;
    }
    try {
      return doc.querySelector(ANCHOR_SELECTOR) !== null;
    } catch {
      return false;
    }
  }
  function profileDocumentNoteKeys(doc, baseUrl) {
    return extractBootstrapNotesFromDocument(doc, "saved", baseUrl).map(
      (note) => note.note_id || note.url || note.title
    );
  }
  function hasDifferentProfileDocumentNotes(notes, previousKeys) {
    if (notes.length === 0) return false;
    if (previousKeys.length === 0) return true;
    const previous = new Set(previousKeys);
    return notes.some((note) => !previous.has(note.note_id || note.url || note.title));
  }
  function limitBootstrapNewNotesToRemainingCapacity(currentNotes, newNotes, maxItemsPerScope) {
    if (newNotes.length === 0) return [];
    const scope = newNotes[0].scope;
    const currentCount = currentNotes.filter((note) => note.scope === scope).length;
    const remaining = Math.max(0, Math.floor(maxItemsPerScope) - currentCount);
    if (remaining <= 0) return [];
    return newNotes.slice(0, remaining);
  }
  function isActiveBootstrapProfileTab(tab) {
    const selected = tab.getAttribute("aria-selected");
    if (selected === "true") return true;
    const className = String(tab.className ?? "").toLowerCase();
    return className.includes("active") || className.includes("selected") || className.includes("current");
  }
  function sliceBalancedObject(source, start) {
    let depth = 0;
    let quote = null;
    let escaped = false;
    for (let i = start; i < source.length; i += 1) {
      const ch = source[i];
      if (quote) {
        if (escaped) {
          escaped = false;
        } else if (ch === "\\") {
          escaped = true;
        } else if (ch === quote) {
          quote = null;
        }
        continue;
      }
      if (ch === '"' || ch === "'") {
        quote = ch;
        continue;
      }
      if (ch === "{") depth += 1;
      if (ch === "}") {
        depth -= 1;
        if (depth === 0) return source.slice(start, i + 1);
      }
    }
    return null;
  }
  function parseInitialStateText(text) {
    const markerIndex = text.indexOf("__INITIAL_STATE__");
    if (markerIndex < 0) return null;
    const objectStart = text.indexOf("{", markerIndex);
    if (objectStart < 0) return null;
    const objectText = sliceBalancedObject(text, objectStart);
    if (!objectText) return null;
    try {
      return JSON.parse(objectText);
    } catch {
      return null;
    }
  }
  var cachedMainWorldState = null;
  var STATE_BRIDGE_SOURCE = "obc-xhs-state";
  function ingestMainWorldStateMessage(data) {
    if (!isRecord(data)) return false;
    const msg = data;
    if (msg.source !== STATE_BRIDGE_SOURCE) return false;
    if (msg.state === void 0 || msg.state === null) return false;
    cachedMainWorldState = msg.state;
    return true;
  }
  if (typeof window !== "undefined") {
    window.addEventListener("message", (event) => {
      if (event.source !== window) return;
      ingestMainWorldStateMessage(event.data);
    });
  }
  function extractBootstrapStateFromDocument(doc) {
    if (cachedMainWorldState !== null) return cachedMainWorldState;
    const win = doc.defaultView;
    if (win?.__INITIAL_STATE__) return win.__INITIAL_STATE__;
    const scripts = doc.querySelectorAll("script");
    for (const script of Array.from(scripts)) {
      const parsed = parseInitialStateText(script.textContent ?? "");
      if (parsed) return parsed;
    }
    return null;
  }
  function mergeBootstrapNotes(notes, scopes, options = {}) {
    const requestedScopes = normalizeBootstrapScopes(scopes);
    const maxItems = Math.max(1, options.maxItemsPerScope ?? DEFAULT_MAX_ITEMS_PER_SCOPE);
    const counts = /* @__PURE__ */ new Map();
    const seen = /* @__PURE__ */ new Set();
    const out = [];
    for (const note of notes) {
      if (!requestedScopes.includes(note.scope)) continue;
      const count = counts.get(note.scope) ?? 0;
      if (count >= maxItems) continue;
      const key = `${note.scope}:${note.note_id || note.url || note.title}`;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      counts.set(note.scope, count + 1);
      out.push(note);
    }
    return out;
  }

  // src/content/xhs/task-executor.ts
  var MAX_URLS = 20;
  var RENDER_WAIT_MS = 5e3;
  var CHECK_INTERVAL_MS = 300;
  var PROFILE_CLICK_DELAY_MS = 150;
  var PROFILE_CONTENT_WAIT_MS = 8e3;
  var ANCHOR_SELECTOR2 = 'a[href*="/explore/"], a[href*="/discovery/item/"]';
  function snapshotAllAnchors(root) {
    const nodes = root.querySelectorAll(ANCHOR_SELECTOR2);
    const out = [];
    nodes.forEach((node) => {
      out.push({ href: node.href, rect: node.getBoundingClientRect() });
    });
    return out;
  }
  function buildLargeViewport(win) {
    const height = win.innerHeight || 900;
    return { top: -500, bottom: height + 500, height: height + 1e3 };
  }
  function waitForCards(doc) {
    return new Promise((resolve) => {
      if (doc.querySelectorAll(ANCHOR_SELECTOR2).length > 0) {
        resolve(true);
        return;
      }
      let settled = false;
      const observer = new MutationObserver(() => {
        if (doc.querySelectorAll(ANCHOR_SELECTOR2).length > 0) {
          settled = true;
          observer.disconnect();
          resolve(true);
        }
      });
      observer.observe(doc.body ?? doc.documentElement, {
        childList: true,
        subtree: true
      });
      const interval = setInterval(() => {
        if (settled) {
          clearInterval(interval);
          return;
        }
        if (doc.querySelectorAll(ANCHOR_SELECTOR2).length > 0) {
          settled = true;
          observer.disconnect();
          clearInterval(interval);
          resolve(true);
        }
      }, CHECK_INTERVAL_MS);
      setTimeout(() => {
        if (!settled) {
          settled = true;
          observer.disconnect();
          clearInterval(interval);
          resolve(doc.querySelectorAll(ANCHOR_SELECTOR2).length > 0);
        }
      }, RENDER_WAIT_MS);
    });
  }
  function waitForBootstrapProfileContent(doc) {
    return new Promise((resolve) => {
      if (hasBootstrapProfileContent(doc)) {
        resolve(true);
        return;
      }
      let settled = false;
      let observer = null;
      let interval = null;
      const finish = (ready) => {
        if (settled) return;
        settled = true;
        observer?.disconnect();
        if (interval !== null) clearInterval(interval);
        resolve(ready);
      };
      try {
        observer = new MutationObserver(() => {
          if (hasBootstrapProfileContent(doc)) finish(true);
        });
        observer.observe(doc.body ?? doc.documentElement, {
          childList: true,
          subtree: true,
          characterData: true
        });
      } catch {
        observer = null;
      }
      interval = setInterval(() => {
        if (hasBootstrapProfileContent(doc)) finish(true);
      }, CHECK_INTERVAL_MS);
      setTimeout(() => {
        finish(hasBootstrapProfileContent(doc));
      }, PROFILE_CONTENT_WAIT_MS);
    });
  }
  function isProfilePage(url) {
    try {
      return new URL(url).pathname.startsWith("/user/profile/");
    } catch {
      return false;
    }
  }
  function buildScopeCounts(scopes, notes = []) {
    const scope_counts = {};
    for (const scope of scopes) {
      scope_counts[scope] = notes.filter((note) => note.scope === scope).length;
    }
    return scope_counts;
  }
  function buildEmptyStateCounts(scopes) {
    const counts = {};
    for (const scope of scopes) counts[scope] = 0;
    return counts;
  }
  function scheduleOwnProfileNavigationClick(doc, win, baseUrl) {
    const profileUrl = extractOwnProfileUrlFromDocument(doc, baseUrl);
    if (!profileUrl) return false;
    win.setTimeout(() => {
      clickOwnProfileAnchorFromDocument(doc, baseUrl, win);
    }, PROFILE_CLICK_DELAY_MS);
    return true;
  }
  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
  async function sendTaskResult(result) {
    try {
      await chrome.runtime.sendMessage({
        action: "XHS_TASK_RESULT",
        data: result
      });
    } catch {
    }
  }
  function profileTabSelector() {
    return [
      "[role='tab']",
      ".tab-item",
      ".reds-tab-item",
      "[class*='tab-item']",
      "[class*='TabItem']",
      "[class*='tabs'] button",
      "[class*='tabs'] a",
      "[class*='Tabs'] button",
      "[class*='Tabs'] a",
      "button",
      "a"
    ].join(", ");
  }
  function normalizedElementText(candidate) {
    return candidate.textContent?.replace(/\s+/g, "").trim() ?? "";
  }
  function isProfileTabLikeElement(candidate) {
    const className = String(candidate.className ?? "").toLowerCase();
    if (candidate.getAttribute("role") === "tab") return true;
    if (className.includes("tab")) return true;
    return candidate.closest(
      "[role='tablist'], .reds-tabs-list, .tabs, [class*='tab-list'], [class*='TabList'], [class*='tabs'], [class*='Tabs']"
    ) !== null;
  }
  function tabTextMatches(text, labels) {
    if (!text || text.length > 16) return false;
    return labels.some((label) => text === label || text.startsWith(label));
  }
  function activateProfileTab(tab, win) {
    tab.scrollIntoView({ block: "center", inline: "center" });
    tab.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: win }));
    tab.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: win }));
    tab.click();
  }
  function extractProfilePageNotes(doc, scope, baseUrl, maxItemsPerScope) {
    const state = extractBootstrapStateFromDocument(doc);
    const stateNotes = state ? extractBootstrapNotesFromState(state, [scope], { baseUrl, maxItemsPerScope }) : [];
    const domNotes = extractBootstrapNotesFromProfileDocument(doc, scope, baseUrl, {
      maxItemsPerScope
    });
    return mergeBootstrapNotes([...stateNotes, ...domNotes], [scope], { maxItemsPerScope });
  }
  function documentScrollMetrics(win, doc) {
    const scrolling = doc.scrollingElement ?? doc.documentElement;
    return {
      target: "document",
      scroll_top: Math.max(0, Math.floor(Math.max(scrolling.scrollTop, win.scrollY || 0))),
      scroll_height: Math.max(0, Math.floor(scrolling.scrollHeight || 0)),
      client_height: Math.max(0, Math.floor(scrolling.clientHeight || win.innerHeight || 0))
    };
  }
  function dispatchWheelLikeScroll(win, target, deltaY) {
    try {
      target.dispatchEvent(
        new WheelEvent("wheel", {
          bubbles: true,
          cancelable: true,
          deltaY,
          deltaMode: 0,
          clientX: Math.floor((win.innerWidth || 1200) / 2),
          clientY: Math.floor((win.innerHeight || 900) * 0.75)
        })
      );
    } catch {
      try {
        target.dispatchEvent(new Event("wheel", { bubbles: true, cancelable: true }));
      } catch {
      }
    }
  }
  function scrollProfilePage(win, doc) {
    const scrollContainer = findBootstrapScrollContainer(doc);
    const scrolling = scrollContainer ?? doc.scrollingElement ?? doc.documentElement;
    const before = scrollContainer ? readBootstrapScrollMetrics(scrollContainer) : documentScrollMetrics(win, doc);
    const currentTop = before.scroll_top;
    const viewportHeight = win.innerHeight || 900;
    const step = Math.max(Math.floor(viewportHeight * 0.8), 640);
    const clientHeight = before.client_height || viewportHeight;
    const nextTop = Math.min(currentTop + step, Math.max(before.scroll_height - clientHeight, 0));
    const wheelTarget = scrollContainer ?? doc.body ?? doc.documentElement;
    const wheelSteps = scrollContainer ? [step] : [220, 260, 240, 280];
    for (const deltaY of wheelSteps) {
      dispatchWheelLikeScroll(win, wheelTarget, deltaY);
      dispatchWheelLikeScroll(win, doc, deltaY);
      dispatchWheelLikeScroll(win, win, deltaY);
    }
    scrolling.scrollTop = nextTop;
    if (!scrollContainer) {
      for (const deltaY of wheelSteps) {
        win.scrollBy({ top: deltaY, behavior: "auto" });
      }
      win.scrollTo({ top: Math.max(nextTop, win.scrollY || 0), behavior: "auto" });
    }
    if (scrollContainer) {
      scrollContainer.dispatchEvent(new Event("scroll", { bubbles: true }));
    }
    win.dispatchEvent(new Event("scroll"));
    const after = scrollContainer ? readBootstrapScrollMetrics(scrollContainer) : documentScrollMetrics(win, doc);
    return {
      target: after.target,
      scroll_top: after.scroll_top,
      scroll_height: after.scroll_height,
      client_height: after.client_height,
      before_top: before.scroll_top,
      after_top: after.scroll_top
    };
  }
  function findProfileTab(doc, labels) {
    const candidates = Array.from(doc.querySelectorAll(profileTabSelector()));
    for (const candidate of candidates) {
      const text = normalizedElementText(candidate);
      if (tabTextMatches(text, labels) && isProfileTabLikeElement(candidate)) {
        return candidate;
      }
    }
    return null;
  }
  async function findProfileTabWithRetry(doc, labels, timeoutMs = 5e3) {
    const deadline = Date.now() + Math.max(0, timeoutMs);
    const immediate = findProfileTab(doc, labels);
    if (immediate) return immediate;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, CHECK_INTERVAL_MS));
      const found = findProfileTab(doc, labels);
      if (found) return found;
    }
    return null;
  }
  function collectProfileTabCandidateTexts(doc) {
    const seen = /* @__PURE__ */ new Set();
    const out = [];
    const candidates = Array.from(doc.querySelectorAll(profileTabSelector()));
    for (const candidate of candidates) {
      if (!isProfileTabLikeElement(candidate)) continue;
      const text = normalizedElementText(candidate);
      if (!text || text.length > 16 || seen.has(text)) continue;
      seen.add(text);
      out.push(text);
    }
    return out.slice(0, 12);
  }
  async function waitForScopeContent(doc, scope, tab, baseUrl, previousKeys, maxItemsPerScope, timeoutMs = 5e3) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const notes2 = extractProfilePageNotes(doc, scope, baseUrl, maxItemsPerScope);
      const changed = hasDifferentProfileDocumentNotes(notes2, previousKeys);
      const active = isActiveBootstrapProfileTab(tab);
      if (changed || active && notes2.length > 0) {
        return {
          notes: notes2,
          changed,
          active,
          before_count: previousKeys.length,
          after_count: notes2.length,
          scroll_rounds: 0,
          stagnant_rounds: 0
        };
      }
      await sleep(250);
    }
    const notes = extractProfilePageNotes(doc, scope, baseUrl, maxItemsPerScope);
    return {
      notes: [],
      changed: false,
      active: isActiveBootstrapProfileTab(tab),
      before_count: previousKeys.length,
      after_count: notes.length,
      scroll_rounds: 0,
      stagnant_rounds: 0
    };
  }
  async function scrollForMoreProfileNotes(taskId, doc, win, scope, baseUrl, initialNotes, maxItemsPerScope, maxScrollRounds, scrollWaitMs, maxStagnantScrollRounds) {
    let notes = mergeBootstrapNotes(initialNotes, [scope], { maxItemsPerScope });
    let stagnantRounds = 0;
    let round = 0;
    const scrollMetrics = [];
    while (bootstrapScrollShouldContinue({
      currentCount: notes.length,
      maxItemsPerScope,
      round,
      maxScrollRounds,
      stagnantRounds,
      maxStagnantScrollRounds
    })) {
      const beforeCount = notes.length;
      const scrollRound = scrollProfilePage(win, doc);
      await sleep(scrollWaitMs);
      const nextNotes = extractProfilePageNotes(doc, scope, baseUrl, maxItemsPerScope);
      const previousKeys = new Set(notes.map((note) => note.note_id || note.url || note.title));
      const newlyAddedCandidates = nextNotes.filter(
        (note) => !previousKeys.has(note.note_id || note.url || note.title)
      );
      const newlyAdded = limitBootstrapNewNotesToRemainingCapacity(
        notes,
        newlyAddedCandidates,
        maxItemsPerScope
      );
      notes = mergeBootstrapNotes([...notes, ...nextNotes], [scope], { maxItemsPerScope });
      if (newlyAdded.length > 0) {
        await sendTaskResult(
          buildBootstrapPartialPayload({
            taskId,
            scope,
            notes: newlyAdded,
            scopeCounts: { [scope]: notes.length },
            round: round + 1
          })
        );
      }
      scrollMetrics.push({
        ...scrollRound,
        round: round + 1,
        added_count: newlyAdded.length,
        total_count: notes.length
      });
      stagnantRounds = notes.length > beforeCount ? 0 : stagnantRounds + 1;
      round += 1;
    }
    return { notes, scrollRounds: round, stagnantRounds, scrollMetrics };
  }
  async function loadProfileTabsForScopes(taskId, scopes, doc, win, baseUrl, maxItemsPerScope, maxScrollRounds, scrollWaitMs, maxStagnantScrollRounds) {
    const domNotes = [];
    const tabResults = {};
    for (const scope of scopes) {
      const labels = bootstrapProfileTabLabels(scope);
      if (!labels) continue;
      const state = extractBootstrapStateFromDocument(doc);
      if (state) {
        const current = extractBootstrapNotesFromState(state, [scope], { maxItemsPerScope });
        if (current.length > 0) continue;
      }
      const tab = await findProfileTabWithRetry(doc, labels);
      if (!tab) continue;
      const previousKeys = profileDocumentNoteKeys(doc, baseUrl);
      activateProfileTab(tab, win);
      const result = await waitForScopeContent(
        doc,
        scope,
        tab,
        baseUrl,
        previousKeys,
        maxItemsPerScope
      );
      if (result.notes.length > 0 && maxScrollRounds > 0) {
        await sendTaskResult(
          buildBootstrapPartialPayload({
            taskId,
            scope,
            notes: result.notes,
            scopeCounts: { [scope]: result.notes.length },
            round: 0
          })
        );
      }
      const scrolled = result.notes.length > 0 && maxScrollRounds > 0 ? await scrollForMoreProfileNotes(
        taskId,
        doc,
        win,
        scope,
        baseUrl,
        result.notes,
        maxItemsPerScope,
        maxScrollRounds,
        scrollWaitMs,
        maxStagnantScrollRounds
      ) : {
        notes: result.notes,
        scrollRounds: 0,
        stagnantRounds: result.stagnant_rounds,
        scrollMetrics: []
      };
      result.notes = scrolled.notes;
      result.after_count = scrolled.notes.length || result.after_count;
      result.scroll_rounds = scrolled.scrollRounds;
      result.stagnant_rounds = scrolled.stagnantRounds;
      result.scroll_metrics = scrolled.scrollMetrics;
      const { notes, ...debugResult } = result;
      tabResults[scope] = debugResult;
      domNotes.push(...result.notes);
    }
    return { notes: domNotes, tabResults };
  }
  function buildTabCandidateDebug(doc) {
    return {
      saved: findProfileTab(doc, bootstrapProfileTabLabels("saved")) !== null,
      liked: findProfileTab(doc, bootstrapProfileTabLabels("liked")) !== null
    };
  }
  async function executeTaskInPage(msg, win, doc) {
    try {
      if (msg.type === "bootstrap_profile") {
        return executeBootstrapTaskInPage(msg, win, doc);
      }
      const found = await waitForCards(doc);
      if (!found) {
        return { task_id: msg.task_id, urls: [], notes: [], status: "empty" };
      }
      const anchors = snapshotAllAnchors(doc);
      const viewport = buildLargeViewport(win);
      const baseUrl = win.location.href;
      const urls = collectInViewportNoteUrls(anchors, viewport, {
        baseUrl,
        toleranceBelowPx: 500,
        toleranceAbovePx: 500
      });
      if (urls.length === 0) {
        return { task_id: msg.task_id, urls: [], notes: [], status: "empty" };
      }
      const urlSet = new Set(urls.slice(0, MAX_URLS));
      const notes = [];
      const anchorEls = doc.querySelectorAll(ANCHOR_SELECTOR2);
      anchorEls.forEach((el) => {
        const meta = extractNoteMetadataFromAnchor(el, baseUrl);
        if (meta && urlSet.has(meta.url)) {
          notes.push(meta);
          urlSet.delete(meta.url);
        }
      });
      const state = extractBootstrapStateFromDocument(doc);
      const selfInfo = state ? extractSelfInfoFromState(state) : null;
      const filteredNotes = filterSelfAuthoredNotes(notes, selfInfo);
      const result = {
        task_id: msg.task_id,
        urls: urls.slice(0, MAX_URLS),
        notes: filteredNotes,
        status: "ok"
      };
      if (selfInfo) {
        result.self_info = selfInfo;
      }
      return result;
    } catch (err) {
      return {
        task_id: msg.task_id,
        urls: [],
        notes: [],
        status: "error",
        error: String(err)
      };
    }
  }
  async function executeBootstrapTaskInPage(msg, win, doc) {
    const scopes = normalizeBootstrapScopes(msg.scopes);
    const maxItemsPerScope = Math.max(1, msg.max_items_per_scope ?? 20);
    const maxScrollRounds = normalizeBootstrapScrollRounds(msg.max_scroll_rounds);
    const scrollWaitMs = normalizeBootstrapScrollWaitMs(msg.scroll_wait_ms);
    const maxStagnantScrollRounds = normalizeBootstrapStagnantScrollRounds(
      msg.max_stagnant_scroll_rounds
    );
    const baseUrl = win.location.href || "https://www.xiaohongshu.com/explore";
    const is_profile_page = isProfilePage(baseUrl);
    const profileContentReady = is_profile_page ? await waitForBootstrapProfileContent(doc) : void 0;
    let state = extractBootstrapStateFromDocument(doc);
    const requested_scopes = [...scopes];
    const initialStateCounts = state ? countBootstrapStateNotesByScope(state, scopes, { baseUrl, maxItemsPerScope }) : buildEmptyStateCounts(scopes);
    const selfInfo = state ? extractSelfInfoFromState(state) : null;
    if (!is_profile_page && (scopes.includes("saved") || scopes.includes("liked"))) {
      const profileUrlFromDocument = extractOwnProfileUrlFromDocument(doc, baseUrl);
      const profileUrlFromState = state ? extractOwnProfileUrlFromState(state, baseUrl) : "";
      const profileUrl = profileUrlFromDocument || profileUrlFromState;
      if (profileUrl) {
        const clickedProfileLink = maxScrollRounds > 0 && profileUrlFromDocument ? scheduleOwnProfileNavigationClick(doc, win, baseUrl) : false;
        return {
          task_id: msg.task_id,
          urls: [],
          notes: [],
          scope_counts: buildScopeCounts(scopes),
          status: "empty",
          next_url: profileUrl,
          debug: buildBootstrapDebugPayload({
            page_url: baseUrl,
            is_profile_page,
            has_initial_state: state !== null,
            requested_scopes,
            state_counts: initialStateCounts,
            profile_url_found: true,
            profile_url_source: profileUrlFromDocument ? "document" : "state",
            next_url_requested: true,
            next_url_clicked: clickedProfileLink,
            self_info: selfInfo ?? void 0
          })
        };
      }
    }
    let domNotes = [];
    let tabResults = {};
    if (is_profile_page) {
      const loaded = await loadProfileTabsForScopes(
        msg.task_id,
        scopes,
        doc,
        win,
        baseUrl,
        maxItemsPerScope,
        maxScrollRounds,
        scrollWaitMs,
        maxStagnantScrollRounds
      );
      domNotes = loaded.notes;
      tabResults = loaded.tabResults;
      state = extractBootstrapStateFromDocument(doc);
    }
    const stateNotes = state ? extractBootstrapNotesFromState(state, scopes, { baseUrl, maxItemsPerScope }) : [];
    const notes = mergeBootstrapNotes([...stateNotes, ...domNotes], scopes, {
      maxItemsPerScope
    });
    const urls = [...new Set(notes.map((note) => note.url).filter(Boolean))];
    const scope_counts = buildScopeCounts(scopes, notes);
    const finalStateCounts = state ? countBootstrapStateNotesByScope(state, scopes, { baseUrl, maxItemsPerScope }) : buildEmptyStateCounts(scopes);
    const finalSelfInfo = selfInfo ?? (state ? extractSelfInfoFromState(state) : null);
    return {
      task_id: msg.task_id,
      urls,
      notes,
      scope_counts,
      status: notes.length > 0 ? "ok" : "empty",
      debug: buildBootstrapDebugPayload({
        page_url: baseUrl,
        is_profile_page,
        has_initial_state: state !== null,
        profile_content_ready: profileContentReady,
        requested_scopes,
        state_counts: finalStateCounts,
        dom_counts: is_profile_page ? buildScopeCounts(scopes, domNotes) : void 0,
        tab_candidate_texts: is_profile_page ? collectProfileTabCandidateTexts(doc) : void 0,
        scroll_candidates: is_profile_page ? collectBootstrapScrollCandidates(doc, 12) : void 0,
        tab_load_results: is_profile_page ? tabResults : void 0,
        profile_url_found: is_profile_page ? void 0 : false,
        profile_url_source: is_profile_page ? void 0 : "",
        next_url_requested: false,
        tab_candidates: is_profile_page ? buildTabCandidateDebug(doc) : void 0,
        self_info: finalSelfInfo ?? void 0
      })
    };
  }
  function registerTaskExecutor() {
    chrome.runtime.onMessage.addListener(
      (message, _sender, sendResponse) => {
        if (message.action !== "XHS_TASK_EXECUTE") return false;
        const data = message.data;
        if (!data?.task_id) return false;
        void executeTaskInPage(data, window, document).then((result) => {
          void sendTaskResult(result);
        });
        return false;
      }
    );
  }

  // src/content/xiaohongshu.ts
  startCollector(xiaohongshuAdapter);
  registerTaskExecutor();
  var TOKEN_FLUSH_DEBOUNCE_MS = 250;
  var TOKEN_BATCH_MAX = 50;
  var tokenBuffer = /* @__PURE__ */ new Map();
  var tokenFlushTimer = null;
  function flushTokensNow() {
    if (tokenFlushTimer !== null) {
      window.clearTimeout(tokenFlushTimer);
      tokenFlushTimer = null;
    }
    if (tokenBuffer.size === 0) return;
    const pairs = [];
    for (const [note_id, xsec_token] of tokenBuffer) {
      pairs.push({ note_id, xsec_token });
      if (pairs.length >= TOKEN_BATCH_MAX) break;
    }
    for (const { note_id } of pairs) tokenBuffer.delete(note_id);
    chrome.runtime.sendMessage({ action: "XHS_TOKENS_OBSERVED", data: { pairs } });
  }
  function scheduleTokenFlush() {
    if (tokenFlushTimer !== null) window.clearTimeout(tokenFlushTimer);
    tokenFlushTimer = window.setTimeout(flushTokensNow, TOKEN_FLUSH_DEBOUNCE_MS);
  }
  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.source !== "obc-xhs-sniffer") return;
    if (!Array.isArray(data.pairs) || data.pairs.length === 0) return;
    for (const pair of data.pairs) {
      if (pair?.note_id && pair?.xsec_token) {
        tokenBuffer.set(pair.note_id, pair.xsec_token);
      }
    }
    scheduleTokenFlush();
  });
  window.addEventListener("pagehide", flushTokensNow);
  window.addEventListener("beforeunload", flushTokensNow);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushTokensNow();
  });
  var PASSIVE_SCROLL_DEBOUNCE_MS = 500;
  var PASSIVE_TOLERANCE_BELOW_PX = 400;
  var PASSIVE_MAX_URLS_PER_BATCH = 20;
  var PASSIVE_ANCHOR_SELECTOR = [
    'a[href*="/explore/"]',
    'a[href*="/discovery/item/"]'
  ].join(",");
  var reportedUrls = /* @__PURE__ */ new Set();
  function readViewport() {
    const height = window.innerHeight || document.documentElement.clientHeight || 0;
    return { top: 0, bottom: height, height };
  }
  function snapshotAnchors() {
    const nodes = document.querySelectorAll(PASSIVE_ANCHOR_SELECTOR);
    const anchors = [];
    nodes.forEach((node) => {
      anchors.push({ href: node.href, rect: node.getBoundingClientRect() });
    });
    return anchors;
  }
  function selfNoteAnchor() {
    const { pathname, search } = window.location;
    if (!pathname.startsWith("/explore/") && !pathname.startsWith("/discovery/item/")) {
      return null;
    }
    const params = new URLSearchParams(search);
    if (!params.has("xsec_token")) return null;
    const rect = new DOMRect(0, 0, 1, 1);
    return { href: window.location.href, rect };
  }
  function readPageSelfInfo() {
    try {
      const state = extractBootstrapStateFromDocument(document);
      if (!state) return null;
      return extractSelfInfoFromState(state);
    } catch {
      return null;
    }
  }
  function runPassiveCollection() {
    const anchors = snapshotAnchors();
    const selfAnchor = selfNoteAnchor();
    if (selfAnchor !== null) {
      anchors.push(selfAnchor);
    }
    const visible = collectInViewportNoteUrls(anchors, readViewport(), {
      baseUrl: window.location.href,
      toleranceBelowPx: PASSIVE_TOLERANCE_BELOW_PX
    });
    const fresh = dedupeObservedUrls(visible, reportedUrls);
    if (fresh.length === 0) return;
    const freshSet = new Set(fresh);
    const baseUrl = window.location.href;
    const notes = [];
    const anchorEls = document.querySelectorAll(PASSIVE_ANCHOR_SELECTOR);
    anchorEls.forEach((el) => {
      const meta = extractNoteMetadataFromAnchor(el, baseUrl);
      if (meta && freshSet.has(meta.url) && notes.length < PASSIVE_MAX_URLS_PER_BATCH) {
        notes.push(meta);
        freshSet.delete(meta.url);
      }
    });
    const selfInfo = readPageSelfInfo();
    const filteredNotes = filterSelfAuthoredNotes(notes, selfInfo);
    const observation = {
      urls: fresh.slice(0, PASSIVE_MAX_URLS_PER_BATCH),
      notes: filteredNotes,
      page_type: classifyXhsPageType(baseUrl),
      observed_at: Date.now(),
      ...selfInfo ? { self_info: selfInfo } : {}
    };
    chrome.runtime.sendMessage({ action: "XHS_URLS_OBSERVED", data: observation });
  }
  var scrollTimer = null;
  window.addEventListener(
    "scroll",
    () => {
      if (scrollTimer !== null) window.clearTimeout(scrollTimer);
      scrollTimer = window.setTimeout(runPassiveCollection, PASSIVE_SCROLL_DEBOUNCE_MS);
    },
    { passive: true }
  );
  window.addEventListener("popstate", () => {
    reportedUrls.clear();
    window.setTimeout(runPassiveCollection, PASSIVE_SCROLL_DEBOUNCE_MS);
  });
  window.setTimeout(runPassiveCollection, PASSIVE_SCROLL_DEBOUNCE_MS);
  console.log(
    "[OpenBiliClaw] Xiaohongshu behavior collector initialized on",
    xiaohongshuAdapter.detectPageType(window.location.href),
    "page"
  );
})();
//# sourceMappingURL=xiaohongshu.js.map
