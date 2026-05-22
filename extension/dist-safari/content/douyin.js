"use strict";
(() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __esm = (fn, res) => function __init() {
    return fn && (res = (0, fn[__getOwnPropNames(fn)[0]])(fn = 0)), res;
  };
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };

  // src/content/dy/task-executor.ts
  var task_executor_exports = {};
  __export(task_executor_exports, {
    BootstrapItemSink: () => BootstrapItemSink,
    DOUYIN_BOOTSTRAP_AWEME_PAGE: () => DOUYIN_BOOTSTRAP_AWEME_PAGE,
    buildBootstrapPartialPayload: () => buildBootstrapPartialPayload,
    buildHotResultPayload: () => buildHotResultPayload,
    buildScopeUrl: () => buildScopeUrl,
    buildSearchResultPayload: () => buildSearchResultPayload,
    dyShouldContinueScroll: () => dyShouldContinueScroll,
    ingestMainWorldFetchMessage: () => ingestMainWorldFetchMessage,
    isValidDouyinBootstrapMessage: () => isValidDouyinBootstrapMessage,
    normalizeBootstrapScrollRounds: () => normalizeBootstrapScrollRounds
  });
  function isValidDouyinBootstrapMessage(event) {
    const data = event?.data;
    if (!data || typeof data !== "object") return false;
    const obj = data;
    if (obj.type !== DOUYIN_BOOTSTRAP_AWEME_PAGE) return false;
    if (!KNOWN_SCOPES.includes(obj.scope)) return false;
    if (!Array.isArray(obj.items)) return false;
    return true;
  }
  function emptyScopeMap(zero) {
    return {
      dy_post: zero(),
      dy_collect: zero(),
      dy_like: zero(),
      dy_follow: zero()
    };
  }
  function itemKey(item) {
    const id = item.scope === "dy_follow" ? item.creator_sec_uid : item.aweme_id;
    return `${item.scope}:${id}`;
  }
  function ingestMainWorldFetchMessage(event, sink) {
    if (!isValidDouyinBootstrapMessage(event)) return [];
    return sink.ingest(event.data.items);
  }
  function buildScopeUrl(scope, secUid) {
    const base = secUid ? `https://www.douyin.com/user/${secUid}` : "https://www.douyin.com/user/self";
    switch (scope) {
      case "dy_post":
        return base;
      case "dy_collect":
        return "https://www.douyin.com/user/self?showTab=favorite_collection";
      case "dy_like":
        return `${base}?showTab=like`;
      case "dy_follow":
        return `${base}?showTab=following`;
    }
  }
  function dyShouldContinueScroll(opts) {
    if (opts.currentCount >= opts.maxItemsPerScope) return false;
    if (opts.round >= opts.maxScrollRounds) return false;
    if (opts.stagnantRounds >= opts.maxStagnantScrollRounds) return false;
    return true;
  }
  function buildBootstrapPartialPayload(input) {
    return {
      task_id: input.taskId,
      status: "partial",
      videos: input.newItems,
      scope_counts: input.scopeCounts,
      debug: { round: input.round, scope: input.scope }
    };
  }
  function buildSearchResultPayload(input) {
    const status = input.error ? "failed" : input.items.length > 0 ? "ok" : "empty";
    return {
      task_id: input.taskId,
      status,
      videos: input.items,
      scope_counts: { dy_search: input.items.length },
      error: input.error,
      debug: {
        keyword: input.keyword,
        api_pages_fetched: input.apiPages,
        dom_items_harvested: input.domItems
      }
    };
  }
  function buildHotResultPayload(input) {
    const status = input.error ? "failed" : input.items.length > 0 ? "ok" : "empty";
    return {
      task_id: input.taskId,
      status,
      videos: input.items,
      scope_counts: { dy_hot: input.items.length },
      error: input.error,
      debug: {
        sentence_id: input.sentenceId,
        word: input.word,
        seed_aweme_id: input.seedAwemeId,
        api_pages_fetched: input.apiPages
      }
    };
  }
  function normalizeBootstrapScrollRounds(rounds) {
    if (rounds === void 0) return 0;
    if (!Number.isFinite(rounds)) return 0;
    const floored = Math.floor(rounds);
    if (floored <= 0) return 0;
    return Math.min(floored, MAX_BOOTSTRAP_SCROLL_ROUNDS);
  }
  var DOUYIN_BOOTSTRAP_AWEME_PAGE, MAX_BOOTSTRAP_SCROLL_ROUNDS, KNOWN_SCOPES, BootstrapItemSink;
  var init_task_executor = __esm({
    "src/content/dy/task-executor.ts"() {
      "use strict";
      DOUYIN_BOOTSTRAP_AWEME_PAGE = "OPENBILICLAW_DOUYIN_AWEME_PAGE";
      MAX_BOOTSTRAP_SCROLL_ROUNDS = 30;
      KNOWN_SCOPES = [
        "dy_post",
        "dy_collect",
        "dy_like",
        "dy_follow"
      ];
      BootstrapItemSink = class {
        maxItemsPerScope;
        seenKeys = /* @__PURE__ */ new Set();
        byScope = emptyScopeMap(() => []);
        constructor(opts) {
          this.maxItemsPerScope = Math.max(0, Math.floor(opts.maxItemsPerScope));
        }
        /**
         * Ingest a batch and return the items that were genuinely new
         * (not duplicates, not over the cap). The caller forwards exactly
         * these to the backend so partial payloads carry only fresh data.
         */
        ingest(items) {
          const newOnes = [];
          for (const item of items) {
            if (!item || !KNOWN_SCOPES.includes(item.scope)) continue;
            const key = itemKey(item);
            if (!key.includes(":") || key.endsWith(":")) continue;
            if (this.seenKeys.has(key)) continue;
            const bucket = this.byScope[item.scope];
            if (bucket.length >= this.maxItemsPerScope) continue;
            this.seenKeys.add(key);
            bucket.push(item);
            newOnes.push(item);
          }
          return newOnes;
        }
        scopeCounts() {
          return {
            dy_post: this.byScope.dy_post.length,
            dy_collect: this.byScope.dy_collect.length,
            dy_like: this.byScope.dy_like.length,
            dy_follow: this.byScope.dy_follow.length
          };
        }
        snapshot() {
          return {
            dy_post: [...this.byScope.dy_post],
            dy_collect: [...this.byScope.dy_collect],
            dy_like: [...this.byScope.dy_like],
            dy_follow: [...this.byScope.dy_follow]
          };
        }
      };
    }
  });

  // src/content/dy/dom-extractor.ts
  var dom_extractor_exports = {};
  __export(dom_extractor_exports, {
    extractAwemeIdFromHref: () => extractAwemeIdFromHref,
    extractDouyinItemsFromDocument: () => extractDouyinItemsFromDocument,
    extractDouyinSearchItemsFromDocument: () => extractDouyinSearchItemsFromDocument,
    extractSecUidFromHref: () => extractSecUidFromHref
  });
  function extractAwemeIdFromHref(href) {
    if (!href) return "";
    const match = href.match(/\/video\/(\d+)/);
    return match ? match[1] ?? "" : "";
  }
  function extractSecUidFromHref(href) {
    if (!href) return "";
    if (/\/user\/self(\?|$)/.test(href)) return "";
    const match = href.match(/\/user\/(MS4w[\w-]+)/);
    return match ? match[1] ?? "" : "";
  }
  function findCardContainer(anchor) {
    const card = anchor.closest(
      [
        'li[class*="ec-card"]',
        'li[class*="card"]',
        'div[class*="ec-card"]',
        'div[class*="card-wrap"]',
        'div[class*="aweme-card"]',
        'div[class*="user-card"]',
        'div[class*="follow-card"]',
        'div[class*="cover-wrap"]',
        "li",
        "article",
        "section"
      ].join(",")
    );
    return card ?? anchor;
  }
  function pickCardTitle(card, anchor) {
    const aria = anchor.getAttribute("aria-label")?.trim() ?? "";
    if (aria) return aria;
    const title = anchor.getAttribute("title")?.trim() ?? "";
    if (title) return title;
    const candidates = [
      'p[class*="title"]',
      'div[class*="title"]',
      'span[class*="title"]',
      'p[class*="desc"]',
      'div[class*="desc"]',
      'span[class*="desc"]',
      "p"
    ];
    for (const sel of candidates) {
      const el = card.querySelector(sel);
      const text = el?.textContent?.trim() ?? "";
      if (text) return text;
    }
    return anchor.textContent?.trim() ?? "";
  }
  function pickAuthorName(card) {
    const candidates = [
      '[class*="author-name"]',
      '[class*="user-name"]',
      '[class*="nickname"]',
      '[class*="author"] [class*="name"]'
    ];
    for (const sel of candidates) {
      const el = card.querySelector(sel);
      const text = el?.textContent?.trim() ?? "";
      if (text) return text;
    }
    return "";
  }
  function pickAuthorSecUid(card) {
    const anchors = Array.from(
      card.querySelectorAll('a[href*="/user/MS4w"]')
    );
    for (const a of anchors) {
      const secUid = extractSecUidFromHref(a.getAttribute("href") ?? a.href ?? "");
      if (secUid) return secUid;
    }
    return "";
  }
  function pickCoverUrl(card) {
    const img = card.querySelector("img");
    if (!img) return "";
    return img.getAttribute("src") || img.getAttribute("data-src") || img.getAttribute("data-original") || "";
  }
  function extractDouyinItemsFromDocument(doc, scope, baseUrl, maxItems) {
    const cap = Math.max(0, Math.floor(maxItems));
    if (cap === 0) return [];
    if (scope === "dy_follow") {
      return extractFollowItems(doc, baseUrl, cap);
    }
    return extractVideoItems(doc, scope, baseUrl, cap);
  }
  function extractDouyinSearchItemsFromDocument(doc, baseUrl, maxItems) {
    const cap = Math.max(0, Math.floor(maxItems));
    if (cap === 0) return [];
    const items = [];
    const seen = /* @__PURE__ */ new Set();
    const anchors = Array.from(
      doc.querySelectorAll('a[href*="/video/"]')
    );
    for (const anchor of anchors) {
      if (items.length >= cap) break;
      const href = anchor.getAttribute("href") ?? anchor.href ?? "";
      const awemeId = extractAwemeIdFromHref(href);
      if (!awemeId || seen.has(awemeId)) continue;
      seen.add(awemeId);
      const card = findCardContainer(anchor);
      items.push({
        scope: "dy_search",
        aweme_id: awemeId,
        url: absolutize(href, baseUrl),
        title: pickCardTitle(card, anchor),
        author: pickAuthorName(card),
        author_sec_uid: pickAuthorSecUid(card),
        cover_url: pickCoverUrl(card)
      });
    }
    return items;
  }
  function extractVideoItems(doc, scope, baseUrl, cap) {
    const items = [];
    const seen = /* @__PURE__ */ new Set();
    const anchors = Array.from(
      doc.querySelectorAll('a[href*="/video/"]')
    );
    for (const anchor of anchors) {
      if (items.length >= cap) break;
      const href = anchor.getAttribute("href") ?? anchor.href ?? "";
      const awemeId = extractAwemeIdFromHref(href);
      if (!awemeId || seen.has(awemeId)) continue;
      seen.add(awemeId);
      const card = findCardContainer(anchor);
      const url = absolutize(href, baseUrl);
      items.push({
        scope,
        aweme_id: awemeId,
        creator_sec_uid: "",
        url,
        title: pickCardTitle(card, anchor),
        author: pickAuthorName(card),
        author_sec_uid: pickAuthorSecUid(card),
        cover_url: pickCoverUrl(card)
      });
    }
    return items;
  }
  function extractFollowItems(doc, baseUrl, cap) {
    const items = [];
    const seen = /* @__PURE__ */ new Set();
    const anchors = Array.from(
      doc.querySelectorAll('a[href*="/user/MS4w"]')
    );
    for (const anchor of anchors) {
      if (items.length >= cap) break;
      const href = anchor.getAttribute("href") ?? anchor.href ?? "";
      const secUid = extractSecUidFromHref(href);
      if (!secUid || seen.has(secUid)) continue;
      seen.add(secUid);
      const card = findCardContainer(anchor);
      const nickname = pickAuthorName(card) || anchor.textContent?.trim() || "";
      const cover = pickCoverUrl(card);
      items.push({
        scope: "dy_follow",
        aweme_id: "",
        creator_sec_uid: secUid,
        url: absolutize(href, baseUrl),
        title: nickname,
        author: nickname,
        author_sec_uid: secUid,
        cover_url: cover
      });
    }
    return items;
  }
  function absolutize(href, baseUrl) {
    if (!href) return "";
    if (/^https?:\/\//.test(href)) return href;
    try {
      return new URL(href, baseUrl).toString();
    } catch {
      return href;
    }
  }
  var init_dom_extractor = __esm({
    "src/content/dy/dom-extractor.ts"() {
      "use strict";
    }
  });

  // src/shared/backend-endpoint.ts
  var DEFAULT_BACKEND_HOST = "127.0.0.1";
  var DEFAULT_BACKEND_PORT = 8420;
  var BACKEND_ENDPOINT_STORAGE_KEY = "popup_backend_endpoint";
  var DEFAULT_ENDPOINT = {
    host: DEFAULT_BACKEND_HOST,
    port: DEFAULT_BACKEND_PORT
  };
  var cached = { ...DEFAULT_ENDPOINT };
  var initialized = false;
  var initPromise = null;
  var storageListenerInstalled = false;
  var subscribers = /* @__PURE__ */ new Set();
  function getStorageLocal() {
    try {
      const chromeApi = globalThis.chrome;
      return chromeApi?.storage?.local ?? null;
    } catch {
      return null;
    }
  }
  function getStorageOnChanged() {
    try {
      const chromeApi = globalThis.chrome;
      return chromeApi?.storage?.onChanged ?? null;
    } catch {
      return null;
    }
  }
  function parseBackendPort(value) {
    if (typeof value === "number" && Number.isInteger(value)) {
      return value >= 1 && value <= 65535 ? value : null;
    }
    if (typeof value === "string" && value.trim() !== "") {
      const trimmed = value.trim();
      if (!/^[0-9]+$/.test(trimmed)) {
        return null;
      }
      const parsed = Number(trimmed);
      return Number.isInteger(parsed) && parsed >= 1 && parsed <= 65535 ? parsed : null;
    }
    return null;
  }
  function coercePort(value) {
    return parseBackendPort(value) ?? DEFAULT_BACKEND_PORT;
  }
  function sanitizeEndpoint(raw) {
    if (typeof raw !== "object" || raw === null) {
      return { ...DEFAULT_ENDPOINT };
    }
    const obj = raw;
    const hostRaw = typeof obj.host === "string" ? obj.host.trim() : "";
    return {
      host: hostRaw || DEFAULT_BACKEND_HOST,
      port: coercePort(obj.port)
    };
  }
  async function loadFromStorage() {
    const storage = getStorageLocal();
    if (!storage?.get) {
      return { ...cached };
    }
    return new Promise((resolve) => {
      try {
        storage.get?.(BACKEND_ENDPOINT_STORAGE_KEY, (items) => {
          const stored = items?.[BACKEND_ENDPOINT_STORAGE_KEY];
          resolve(stored === void 0 ? { ...cached } : sanitizeEndpoint(stored));
        });
      } catch {
        resolve({ ...cached });
      }
    });
  }
  function installStorageChangeListener() {
    if (storageListenerInstalled) return;
    const onChanged = getStorageOnChanged();
    if (!onChanged?.addListener) return;
    try {
      onChanged.addListener((changes, area) => {
        if (area !== "local") return;
        const change = changes[BACKEND_ENDPOINT_STORAGE_KEY];
        if (!change) return;
        const next = sanitizeEndpoint(change.newValue);
        cached = next;
        initialized = true;
        for (const cb of subscribers) {
          try {
            cb(next);
          } catch {
          }
        }
      });
      storageListenerInstalled = true;
    } catch {
    }
  }
  async function ensureLoaded() {
    if (initialized) return cached;
    if (initPromise) return initPromise;
    initPromise = (async () => {
      const endpoint = await loadFromStorage();
      cached = endpoint;
      initialized = true;
      installStorageChangeListener();
      return endpoint;
    })();
    return initPromise;
  }
  async function apiUrl(path) {
    const ep = await ensureLoaded();
    const suffix = path.startsWith("/") ? path : `/${path}`;
    return `http://${ep.host}:${ep.port}/api${suffix}`;
  }

  // src/content/douyin.ts
  function debugLog(event, data) {
    void (async () => {
      try {
        await fetch(await apiUrl("/sources/_debug/log"), {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ source: "dy-cs", event, data: data ?? null })
        });
      } catch {
      }
    })();
  }
  function reinjectFetchTap() {
    if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.getURL) return;
    const script = document.createElement("script");
    script.src = chrome.runtime.getURL("dist/main/dy-fetch-tap.js");
    script.onload = () => script.remove();
    script.onerror = () => script.remove();
    (document.head || document.documentElement).appendChild(script);
  }
  async function loadTaskExecutorHelpers() {
    return await Promise.resolve().then(() => (init_task_executor(), task_executor_exports));
  }
  async function loadDomExtractor() {
    return await Promise.resolve().then(() => (init_dom_extractor(), dom_extractor_exports));
  }
  var SCROLL_DELAY_MS = 1500;
  var POST_INSTALL_SETTLE_MS = 800;
  var _lastFetchTapInstallStatus = "unknown";
  var _installMessagesReceived = 0;
  var _detectedSecUid = "";
  if (typeof window !== "undefined") {
    window.addEventListener("message", (event) => {
      const data = event?.data;
      if (!data || typeof data !== "object") return;
      if (data.type === "OPENBILICLAW_DOUYIN_FETCH_TAP_INSTALL") {
        _installMessagesReceived += 1;
        const s = String(data.status ?? "");
        if (s === "installed" || s === "skipped_no_sdk") {
          _lastFetchTapInstallStatus = s;
        }
        return;
      }
      if (data.type === "OPENBILICLAW_DOUYIN_SEC_UID") {
        const secUid = String(data.secUid ?? "");
        if (secUid && secUid !== _detectedSecUid) {
          _detectedSecUid = secUid;
          debugLog("sec_uid_detected", { secUid });
        }
        return;
      }
      if (data.type === "OPENBILICLAW_DOUYIN_URL_PROBE") {
        const probe = data;
        debugLog("url_probe", {
          transport: String(probe.transport ?? ""),
          url: String(probe.url ?? ""),
          classified: probe.classified ?? null
        });
      }
    });
  }
  async function harvestScopeViaApiBridge(scope, secUid, maxItems, timeoutMs = 9e4) {
    return new Promise((resolve) => {
      const requestId = `obc_dy_api_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        window.removeEventListener("message", onMessage);
        resolve({ items: [], pages: 0, error: "timeout" });
      }, timeoutMs);
      const onMessage = (event) => {
        const data = event?.data;
        if (!data || typeof data !== "object") return;
        if (data.type !== "OPENBILICLAW_DOUYIN_API_RESPONSE") return;
        if (data.requestId !== requestId) return;
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        window.removeEventListener("message", onMessage);
        const items = Array.isArray(data.items) ? data.items : [];
        const pages = Number(data.pages_fetched ?? 0);
        const error = typeof data.error === "string" ? data.error : void 0;
        resolve({ items, pages, error });
      };
      window.addEventListener("message", onMessage);
      window.postMessage(
        {
          type: "OPENBILICLAW_DOUYIN_API_REQUEST",
          requestId,
          scope,
          secUid,
          maxItems
        },
        window.location.origin
      );
    });
  }
  async function harvestSearchViaApiBridge(keyword, maxItems, timeoutMs = 45e3) {
    return new Promise((resolve) => {
      const requestId = `obc_dy_search_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        window.removeEventListener("message", onMessage);
        resolve({ items: [], pages: 0, error: "timeout" });
      }, timeoutMs);
      const onMessage = (event) => {
        const data = event?.data;
        if (!data || typeof data !== "object") return;
        if (data.type !== "OPENBILICLAW_DOUYIN_SEARCH_API_RESPONSE") return;
        if (data.requestId !== requestId) return;
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        window.removeEventListener("message", onMessage);
        const items = Array.isArray(data.items) ? data.items : [];
        const pages = Number(data.pages_fetched ?? 0);
        const error = typeof data.error === "string" ? data.error : void 0;
        resolve({ items, pages, error });
      };
      window.addEventListener("message", onMessage);
      window.postMessage(
        {
          type: "OPENBILICLAW_DOUYIN_SEARCH_API_REQUEST",
          requestId,
          keyword,
          maxItems
        },
        window.location.origin
      );
    });
  }
  async function harvestHotRelatedViaApiBridge(seedAwemeId, maxItems, sentenceId, word, timeoutMs = 45e3) {
    return new Promise((resolve) => {
      const requestId = `obc_dy_hot_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        window.removeEventListener("message", onMessage);
        resolve({ items: [], pages: 0, error: "timeout" });
      }, timeoutMs);
      const onMessage = (event) => {
        const data = event?.data;
        if (!data || typeof data !== "object") return;
        if (data.type !== "OPENBILICLAW_DOUYIN_HOT_API_RESPONSE") return;
        if (data.requestId !== requestId) return;
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        window.removeEventListener("message", onMessage);
        const items = Array.isArray(data.items) ? data.items : [];
        const pages = Number(data.pages_fetched ?? 0);
        const error = typeof data.error === "string" ? data.error : void 0;
        resolve({ items, pages, error });
      };
      window.addEventListener("message", onMessage);
      window.postMessage(
        {
          type: "OPENBILICLAW_DOUYIN_HOT_API_REQUEST",
          requestId,
          seedAwemeId,
          maxItems,
          sentenceId,
          word
        },
        window.location.origin
      );
    });
  }
  async function harvestFeedViaApiBridge(maxItems, timeoutMs = 45e3) {
    return new Promise((resolve) => {
      const requestId = `obc_dy_feed_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        window.removeEventListener("message", onMessage);
        resolve({ items: [], pages: 0, error: "timeout" });
      }, timeoutMs);
      const onMessage = (event) => {
        const data = event?.data;
        if (!data || typeof data !== "object") return;
        if (data.type !== "OPENBILICLAW_DOUYIN_FEED_API_RESPONSE") return;
        if (data.requestId !== requestId) return;
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        window.removeEventListener("message", onMessage);
        const items = Array.isArray(data.items) ? data.items : [];
        const pages = Number(data.pages_fetched ?? 0);
        const error = typeof data.error === "string" ? data.error : void 0;
        resolve({ items, pages, error });
      };
      window.addEventListener("message", onMessage);
      window.postMessage(
        {
          type: "OPENBILICLAW_DOUYIN_FEED_API_REQUEST",
          requestId,
          maxItems
        },
        window.location.origin
      );
    });
  }
  function extractAwemeIdFromLocationHref(href) {
    const match = href.match(/\/video\/(\d+)/);
    return match?.[1] ?? "";
  }
  async function waitForCurrentVideoAwemeId(timeoutMs = 8e3) {
    for (let waited = 0; waited <= timeoutMs; waited += 200) {
      const awemeId = extractAwemeIdFromLocationHref(location.href);
      if (awemeId) return awemeId;
      await sleep(200);
    }
    return "";
  }
  function dedupeSearchItems(items, maxItems) {
    const cap = Math.max(0, Math.floor(maxItems));
    const seen = /* @__PURE__ */ new Set();
    const result = [];
    for (const item of items) {
      const key = item.aweme_id || `${item.title}:${item.author}`;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      result.push(item);
      if (result.length >= cap) break;
    }
    return result;
  }
  async function triggerSearchUi(keyword) {
    let input = null;
    for (let waited = 0; waited < 5e3 && !input; waited += 200) {
      const inputs = Array.from(
        document.querySelectorAll("input, textarea")
      );
      input = inputs.find((el) => (el.getAttribute("placeholder") ?? "").includes("\u641C\u7D22")) ?? inputs[0] ?? null;
      if (!input) await sleep(200);
    }
    if (!input) return false;
    input.focus();
    const proto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) {
      setter.call(input, keyword);
    } else {
      input.value = keyword;
    }
    input.dispatchEvent(
      new InputEvent("input", {
        bubbles: true,
        inputType: "insertText",
        data: keyword
      })
    );
    input.dispatchEvent(new Event("change", { bubbles: true }));
    const buttons = Array.from(document.querySelectorAll("button, [role='button']"));
    const button = buttons.find((el) => (el.textContent ?? "").trim().includes("\u641C\u7D22"));
    if (button) {
      button.click();
      return true;
    }
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    input.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", bubbles: true }));
    return true;
  }
  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
  function findProfileLink() {
    const candidates = Array.from(
      document.querySelectorAll(
        'a[href="/user/self"], a[href^="/user/MS4w"], a[href*="/user/self"], a[href*="/user/"]'
      )
    );
    for (const anchor of candidates) {
      const href = anchor.getAttribute("href") ?? "";
      if (href.includes("?showTab=")) continue;
      return anchor;
    }
    const dataSelectors = [
      '[data-e2e="profile-icon"]',
      '[data-e2e="user-tab-self"]',
      '[data-e2e="user-info"]',
      '[data-e2e="my-tab"]'
    ];
    for (const sel of dataSelectors) {
      const el = document.querySelector(sel);
      if (el && "click" in el) return el;
    }
    const profileLabels = ["\u6211", "\u6211\u7684", "\u4E2A\u4EBA\u4E3B\u9875"];
    const textCandidates = Array.from(
      document.querySelectorAll(
        'a, button, [role="link"], [role="button"], [data-e2e]'
      )
    );
    for (const el of textCandidates) {
      const text = el.textContent?.trim() ?? "";
      if (profileLabels.includes(text)) return el;
    }
    return null;
  }
  async function clickToScope(scope) {
    const report = {
      page_url: location.href,
      profile_link_found: false,
      sub_tab_found: false
    };
    const onProfile = location.pathname.startsWith("/user/");
    if (!onProfile) {
      const profileLink = findProfileLink();
      report.profile_link_found = profileLink !== null;
      if (profileLink) {
        profileLink.click();
      } else {
        window.history.pushState({}, "", "/user/self");
        window.dispatchEvent(new PopStateEvent("popstate"));
      }
      await sleep(2500);
      report.page_url = location.href;
    }
    const queryMap = {
      dy_post: "",
      dy_collect: "?showTab=favorite_collection",
      dy_like: "?showTab=like",
      dy_follow: "?showTab=following"
    };
    const targetUrl = "/user/self" + queryMap[scope];
    const wantedSearch = queryMap[scope];
    const clickedTab = clickScopeSubTab(scope);
    report.sub_tab_found = clickedTab;
    if (clickedTab) {
      await sleep(1500);
    }
    const onTargetTab = wantedSearch === "" ? !location.search.includes("showTab=") : location.search.includes(wantedSearch.replace("?", ""));
    if (!onTargetTab) {
      const currentRelative = location.pathname + location.search;
      if (currentRelative === targetUrl) {
        window.history.pushState({}, "", "/user/self?_obc=" + Date.now());
        window.dispatchEvent(new PopStateEvent("popstate"));
        await sleep(400);
      }
      window.history.pushState({}, "", targetUrl);
      window.dispatchEvent(new PopStateEvent("popstate"));
      await sleep(2e3);
    }
    report.page_url = location.href;
    return report;
  }
  function clickScopeSubTab(scope) {
    const dataSelectors = {
      dy_post: [
        '[data-e2e="user-tab-self"]',
        '[data-e2e="user-tab-post"]',
        '[data-e2e="user-tab-work"]'
      ],
      dy_collect: [
        '[data-e2e="user-favorite-tab"]',
        '[data-e2e="user-tab-favorite_collection"]',
        '[data-e2e="user-tab-favorite"]',
        'a[href*="favorite_collection"]'
      ],
      dy_like: [
        '[data-e2e="user-like-tab"]',
        '[data-e2e="user-tab-like"]',
        'a[href*="showTab=like"]'
      ],
      dy_follow: [
        '[data-e2e="user-following-tab"]',
        '[data-e2e="user-tab-following"]',
        'a[href*="showTab=following"]'
      ]
    };
    for (const sel of dataSelectors[scope]) {
      const el = document.querySelector(sel);
      if (el) {
        fireRealClick(el);
        return true;
      }
    }
    const labelMap = {
      dy_post: "\u4F5C\u54C1",
      dy_collect: "\u6536\u85CF",
      dy_like: "\u559C\u6B22",
      dy_follow: "\u5173\u6CE8"
    };
    const label = labelMap[scope];
    const candidates = Array.from(
      document.querySelectorAll('a, button, [role="tab"], [class*="tab"]')
    );
    for (const el of candidates) {
      if (el.textContent?.trim() === label) {
        fireRealClick(el);
        return true;
      }
    }
    return false;
  }
  function fireRealClick(el) {
    el.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true, composed: true })
    );
  }
  function scrollScopeListToEnd(scope) {
    const selector = scope === "dy_follow" ? 'a[href*="/user/MS4w"]' : 'a[href*="/video/"]';
    const anchors = document.querySelectorAll(selector);
    if (anchors.length === 0) return false;
    const last = anchors[anchors.length - 1];
    if (!last) return false;
    try {
      last.scrollIntoView({ block: "end", inline: "nearest", behavior: "auto" });
    } catch {
      return false;
    }
    return true;
  }
  function findScopeScrollerHeight() {
    const last = document.querySelector(
      'a[href*="/video/"]:last-of-type, a[href*="/user/MS4w"]:last-of-type'
    );
    let cur = last;
    while (cur && cur !== document.body) {
      if (cur.scrollHeight > cur.clientHeight + 5) return cur.scrollHeight;
      cur = cur.parentElement;
    }
    return 0;
  }
  var END_OF_FEED_PHRASES = [
    "\u6682\u65F6\u6CA1\u6709\u66F4\u591A",
    "\u6CA1\u6709\u66F4\u591A\u4E86",
    "\u6CA1\u6709\u66F4\u591A\u5185\u5BB9",
    "\u5DF2\u52A0\u8F7D\u5168\u90E8",
    "\u5DF2\u7ECF\u5230\u5E95",
    "\u5230\u5E95\u5566",
    "\u5DF2\u7ECF\u5230\u5E95\u5566",
    "no more",
    "the end"
  ];
  function isTextNodeRenderedVisible(el) {
    if (!el.offsetParent && el !== document.body) return false;
    if (el.offsetWidth === 0 || el.offsetHeight === 0) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return false;
    if (parseFloat(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return false;
    if (rect.bottom < window.innerHeight * 0.4) return false;
    return true;
  }
  function detectEndOfFeed() {
    const candidates = Array.from(
      document.querySelectorAll(
        'div, span, p, [class*="loading"], [class*="end"], [class*="finish"]'
      )
    );
    for (const el of candidates) {
      const text = (el.textContent ?? "").trim();
      if (!text || text.length > 30) continue;
      let matched = "";
      for (const phrase of END_OF_FEED_PHRASES) {
        if (text.includes(phrase)) {
          matched = phrase;
          break;
        }
      }
      if (!matched) continue;
      if (!isTextNodeRenderedVisible(el)) continue;
      return text;
    }
    return "";
  }
  async function runScope(msg) {
    debugLog("runScope:start", {
      scope: msg.scope,
      page_url: location.href,
      inject_status: msg.debug_inject_status
    });
    const { BootstrapItemSink: BootstrapItemSink2, dyShouldContinueScroll: dyShouldContinueScroll2, ingestMainWorldFetchMessage: ingestMainWorldFetchMessage2 } = await loadTaskExecutorHelpers();
    const { extractDouyinItemsFromDocument: extractDouyinItemsFromDocument2 } = await loadDomExtractor();
    const sink = new BootstrapItemSink2({ maxItemsPerScope: msg.max_items_per_scope });
    const allItems = [];
    let awemeMessagesReceived = 0;
    let domItemsHarvested = 0;
    let apiItemsHarvested = 0;
    let apiPagesFetched = 0;
    let apiError = "";
    const onMessage = (event) => {
      const data = event?.data;
      if (data && typeof data === "object" && data.type === "OPENBILICLAW_DOUYIN_AWEME_PAGE") {
        awemeMessagesReceived += 1;
      }
      const newOnes = ingestMainWorldFetchMessage2(event, sink);
      for (const item of newOnes) {
        if (item.scope === msg.scope) allItems.push(item);
      }
    };
    window.addEventListener("message", onMessage);
    const harvestDomSnapshot = () => {
      const dom = extractDouyinItemsFromDocument2(
        document,
        msg.scope,
        location.origin,
        msg.max_items_per_scope
      );
      if (dom.length === 0) return;
      const newOnes = sink.ingest(dom);
      for (const item of newOnes) {
        if (item.scope === msg.scope) allItems.push(item);
      }
      domItemsHarvested += newOnes.length;
    };
    let clickReport = {
      page_url: location.href,
      profile_link_found: false,
      sub_tab_found: false
    };
    let endOfFeedPhrase = "";
    try {
      clickReport = await clickToScope(msg.scope);
      debugLog("runScope:clickToScope_done", { scope: msg.scope, clickReport });
      reinjectFetchTap();
      debugLog("runScope:reinjected_fetch_tap");
      await sleep(POST_INSTALL_SETTLE_MS);
      harvestDomSnapshot();
      for (let waited = 0; waited < 4e3 && !_detectedSecUid; waited += 200) {
        await sleep(200);
      }
      if (_detectedSecUid) {
        const apiResult = await harvestScopeViaApiBridge(
          msg.scope,
          _detectedSecUid,
          msg.max_items_per_scope
        );
        apiPagesFetched = apiResult.pages;
        apiError = apiResult.error ?? "";
        if (apiResult.items.length > 0) {
          const newOnes = sink.ingest(apiResult.items);
          apiItemsHarvested += newOnes.length;
          for (const item of newOnes) {
            if (item.scope === msg.scope) allItems.push(item);
          }
        }
        debugLog("api_harvest_done", {
          scope: msg.scope,
          pages: apiResult.pages,
          items_total: apiResult.items.length,
          items_new: apiItemsHarvested,
          error: apiError
        });
      } else {
        debugLog("api_harvest_skipped", { scope: msg.scope, reason: "no_sec_uid" });
      }
      const anchorSelector = msg.scope === "dy_follow" ? 'a[href*="/user/MS4w"]' : 'a[href*="/video/"]';
      let stagnantRounds = 0;
      for (let round = 0; round < msg.max_scroll_rounds; round += 1) {
        const beforeCount = sink.scopeCounts()[msg.scope];
        const beforeDomSize = document.querySelectorAll(anchorSelector).length;
        scrollScopeListToEnd(msg.scope);
        window.scrollBy({ top: window.innerHeight * 2, behavior: "auto" });
        await sleep(SCROLL_DELAY_MS);
        harvestDomSnapshot();
        const afterCount = sink.scopeCounts()[msg.scope];
        const afterDomSize = document.querySelectorAll(anchorSelector).length;
        endOfFeedPhrase = detectEndOfFeed();
        debugLog("scroll_round", {
          scope: msg.scope,
          round,
          beforeCount,
          afterCount,
          beforeDomSize,
          afterDomSize,
          scrollY: window.scrollY,
          innerScrollerHeight: findScopeScrollerHeight(),
          endOfFeed: endOfFeedPhrase
        });
        stagnantRounds = afterCount > beforeCount ? 0 : stagnantRounds + 1;
        if (endOfFeedPhrase) break;
        if (!dyShouldContinueScroll2({
          currentCount: afterCount,
          maxItemsPerScope: msg.max_items_per_scope,
          round: round + 1,
          maxScrollRounds: msg.max_scroll_rounds,
          stagnantRounds,
          maxStagnantScrollRounds: msg.max_stagnant_scroll_rounds
        })) {
          break;
        }
      }
      harvestDomSnapshot();
      return {
        task_id: msg.task_id,
        scope: msg.scope,
        items: allItems,
        scope_count: sink.scopeCounts()[msg.scope],
        status: allItems.length > 0 ? "ok" : "empty",
        debug: {
          fetch_tap_install_status: _lastFetchTapInstallStatus,
          aweme_messages_received: awemeMessagesReceived,
          install_messages_received: _installMessagesReceived,
          dom_items_harvested: domItemsHarvested,
          api_items_harvested: apiItemsHarvested,
          api_pages_fetched: apiPagesFetched,
          api_error: apiError,
          sec_uid: _detectedSecUid,
          end_of_feed: endOfFeedPhrase,
          inject_status: msg.debug_inject_status,
          page_url: clickReport.page_url,
          profile_link_found: clickReport.profile_link_found,
          sub_tab_found: clickReport.sub_tab_found
        }
      };
    } catch (err) {
      return {
        task_id: msg.task_id,
        scope: msg.scope,
        items: allItems,
        scope_count: sink.scopeCounts()[msg.scope],
        status: "failed",
        error: String(err),
        debug: {
          fetch_tap_install_status: _lastFetchTapInstallStatus,
          aweme_messages_received: awemeMessagesReceived,
          install_messages_received: _installMessagesReceived,
          dom_items_harvested: domItemsHarvested,
          api_items_harvested: apiItemsHarvested,
          api_pages_fetched: apiPagesFetched,
          api_error: apiError,
          sec_uid: _detectedSecUid,
          end_of_feed: endOfFeedPhrase,
          inject_status: msg.debug_inject_status,
          page_url: clickReport.page_url,
          profile_link_found: clickReport.profile_link_found,
          sub_tab_found: clickReport.sub_tab_found
        }
      };
    } finally {
      window.removeEventListener("message", onMessage);
    }
  }
  async function runSearch(msg) {
    const { extractDouyinSearchItemsFromDocument: extractDouyinSearchItemsFromDocument2 } = await loadDomExtractor();
    const maxItems = Math.max(1, Math.floor(msg.max_items));
    let apiPagesFetched = 0;
    let apiItemsHarvested = 0;
    let domItemsHarvested = 0;
    let apiError = "";
    let uiTriggered = false;
    const allItems = [];
    const onSearchTapMessage = (event) => {
      const data = event?.data;
      if (!data || typeof data !== "object") return;
      if (data.type !== "OPENBILICLAW_DOUYIN_SEARCH_PAGE") return;
      if (!Array.isArray(data.items)) return;
      allItems.push(...data.items);
    };
    window.addEventListener("message", onSearchTapMessage);
    try {
      reinjectFetchTap();
      await sleep(POST_INSTALL_SETTLE_MS);
      uiTriggered = await triggerSearchUi(msg.keyword);
      debugLog("search_ui_triggered", { keyword: msg.keyword, uiTriggered });
      await sleep(2e3);
      const apiResult = await harvestSearchViaApiBridge(msg.keyword, maxItems);
      apiPagesFetched = apiResult.pages;
      apiError = apiResult.error ?? "";
      apiItemsHarvested = apiResult.items.length;
      allItems.push(...apiResult.items);
      for (let round = 0; round < 4 && allItems.length < maxItems; round += 1) {
        const domItems = extractDouyinSearchItemsFromDocument2(
          document,
          location.origin,
          maxItems
        );
        domItemsHarvested = Math.max(domItemsHarvested, domItems.length);
        allItems.push(...domItems);
        window.scrollBy({ top: window.innerHeight * 2, behavior: "auto" });
        await sleep(1e3);
      }
      const items = dedupeSearchItems(allItems, maxItems);
      return {
        task_id: msg.task_id,
        keyword: msg.keyword,
        items,
        scope_count: items.length,
        status: items.length > 0 ? "ok" : "empty",
        debug: {
          fetch_tap_install_status: _lastFetchTapInstallStatus,
          api_pages_fetched: apiPagesFetched,
          api_items_harvested: apiItemsHarvested,
          dom_items_harvested: domItemsHarvested,
          api_error: apiError,
          ui_triggered: uiTriggered,
          inject_status: msg.debug_inject_status,
          page_url: location.href
        }
      };
    } catch (err) {
      const items = dedupeSearchItems(allItems, maxItems);
      return {
        task_id: msg.task_id,
        keyword: msg.keyword,
        items,
        scope_count: items.length,
        status: items.length > 0 ? "ok" : "failed",
        error: items.length > 0 ? void 0 : String(err),
        debug: {
          fetch_tap_install_status: _lastFetchTapInstallStatus,
          api_pages_fetched: apiPagesFetched,
          api_items_harvested: apiItemsHarvested,
          dom_items_harvested: domItemsHarvested,
          api_error: apiError || String(err),
          ui_triggered: uiTriggered,
          inject_status: msg.debug_inject_status,
          page_url: location.href
        }
      };
    } finally {
      window.removeEventListener("message", onSearchTapMessage);
    }
  }
  async function runHot(msg) {
    const maxItems = Math.max(1, Math.floor(msg.max_items));
    let apiPagesFetched = 0;
    let apiItemsHarvested = 0;
    let apiError = "";
    let seedAwemeId = "";
    const allItems = [];
    try {
      reinjectFetchTap();
      await sleep(POST_INSTALL_SETTLE_MS);
      seedAwemeId = await waitForCurrentVideoAwemeId();
      if (!seedAwemeId) {
        throw new Error("hot_seed_aweme_id_missing");
      }
      const apiResult = await harvestHotRelatedViaApiBridge(
        seedAwemeId,
        maxItems,
        msg.sentence_id,
        msg.word
      );
      apiPagesFetched = apiResult.pages;
      apiError = apiResult.error ?? "";
      apiItemsHarvested = apiResult.items.length;
      allItems.push(...apiResult.items);
      const items = dedupeSearchItems(allItems, maxItems);
      return {
        task_id: msg.task_id,
        sentence_id: msg.sentence_id,
        word: msg.word,
        items,
        scope_count: items.length,
        status: items.length > 0 ? "ok" : "empty",
        debug: {
          fetch_tap_install_status: _lastFetchTapInstallStatus,
          api_pages_fetched: apiPagesFetched,
          api_items_harvested: apiItemsHarvested,
          api_error: apiError,
          seed_aweme_id: seedAwemeId,
          inject_status: msg.debug_inject_status,
          page_url: location.href
        }
      };
    } catch (err) {
      const items = dedupeSearchItems(allItems, maxItems);
      return {
        task_id: msg.task_id,
        sentence_id: msg.sentence_id,
        word: msg.word,
        items,
        scope_count: items.length,
        status: items.length > 0 ? "ok" : "failed",
        error: items.length > 0 ? void 0 : String(err),
        debug: {
          fetch_tap_install_status: _lastFetchTapInstallStatus,
          api_pages_fetched: apiPagesFetched,
          api_items_harvested: apiItemsHarvested,
          api_error: apiError || String(err),
          seed_aweme_id: seedAwemeId,
          inject_status: msg.debug_inject_status,
          page_url: location.href
        }
      };
    }
  }
  async function runFeed(msg) {
    const { extractDouyinSearchItemsFromDocument: extractDouyinSearchItemsFromDocument2 } = await loadDomExtractor();
    const maxItems = Math.max(1, Math.floor(msg.max_items));
    let apiPagesFetched = 0;
    let apiItemsHarvested = 0;
    let domItemsHarvested = 0;
    let apiError = "";
    const allItems = [];
    try {
      reinjectFetchTap();
      await sleep(POST_INSTALL_SETTLE_MS);
      const apiResult = await harvestFeedViaApiBridge(maxItems);
      apiPagesFetched = apiResult.pages;
      apiError = apiResult.error ?? "";
      apiItemsHarvested = apiResult.items.length;
      allItems.push(...apiResult.items);
      for (let round = 0; round < 4 && allItems.length < maxItems; round += 1) {
        const domItems = extractDouyinSearchItemsFromDocument2(
          document,
          location.origin,
          maxItems
        ).map((item) => ({ ...item, scope: "dy_feed" }));
        domItemsHarvested = Math.max(domItemsHarvested, domItems.length);
        allItems.push(...domItems);
        window.scrollBy({ top: window.innerHeight * 2, behavior: "auto" });
        await sleep(1e3);
      }
      const items = dedupeSearchItems(allItems, maxItems);
      return {
        task_id: msg.task_id,
        items,
        scope_count: items.length,
        status: items.length > 0 ? "ok" : "empty",
        debug: {
          fetch_tap_install_status: _lastFetchTapInstallStatus,
          api_pages_fetched: apiPagesFetched,
          api_items_harvested: apiItemsHarvested,
          dom_items_harvested: domItemsHarvested,
          api_error: apiError,
          inject_status: msg.debug_inject_status,
          page_url: location.href
        }
      };
    } catch (err) {
      const items = dedupeSearchItems(allItems, maxItems);
      return {
        task_id: msg.task_id,
        items,
        scope_count: items.length,
        status: items.length > 0 ? "ok" : "failed",
        error: items.length > 0 ? void 0 : String(err),
        debug: {
          fetch_tap_install_status: _lastFetchTapInstallStatus,
          api_pages_fetched: apiPagesFetched,
          api_items_harvested: apiItemsHarvested,
          dom_items_harvested: domItemsHarvested,
          api_error: apiError || String(err),
          inject_status: msg.debug_inject_status,
          page_url: location.href
        }
      };
    }
  }
  function isValidScopeExecuteMessage(value) {
    if (!value || typeof value !== "object") return false;
    const v = value;
    if (typeof v.task_id !== "string" || !v.task_id) return false;
    const KNOWN = ["dy_post", "dy_collect", "dy_like", "dy_follow"];
    if (!KNOWN.includes(v.scope)) return false;
    if (typeof v.max_items_per_scope !== "number") return false;
    if (typeof v.max_scroll_rounds !== "number") return false;
    if (typeof v.max_stagnant_scroll_rounds !== "number") return false;
    return true;
  }
  function isValidSearchExecuteMessage(value) {
    if (!value || typeof value !== "object") return false;
    const v = value;
    if (typeof v.task_id !== "string" || !v.task_id) return false;
    if (typeof v.keyword !== "string" || !v.keyword.trim()) return false;
    if (typeof v.max_items !== "number") return false;
    return true;
  }
  function isValidHotExecuteMessage(value) {
    if (!value || typeof value !== "object") return false;
    const v = value;
    if (typeof v.task_id !== "string" || !v.task_id) return false;
    if (typeof v.sentence_id !== "string" || !v.sentence_id.trim()) return false;
    if (typeof v.max_items !== "number") return false;
    return true;
  }
  function isValidFeedExecuteMessage(value) {
    if (!value || typeof value !== "object") return false;
    const v = value;
    if (typeof v.task_id !== "string" || !v.task_id) return false;
    if (typeof v.max_items !== "number") return false;
    return Number.isFinite(v.max_items) && v.max_items > 0;
  }
  function registerDyScopeExecutor() {
    if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.onMessage) return;
    chrome.runtime.onMessage.addListener(
      (message, _sender, sendResponse) => {
        if (message.action !== "DY_SCOPE_EXECUTE") return false;
        const data = message.data;
        if (!isValidScopeExecuteMessage(data)) {
          debugLog("listener:invalid_scope_execute", { message });
          return false;
        }
        debugLog("listener:DY_SCOPE_EXECUTE_received", {
          scope: data.scope,
          page_url: location.href
        });
        void runScope(data).then((result) => {
          debugLog("runScope:returning", {
            scope: result.scope,
            status: result.status,
            items_count: result.items.length
          });
          chrome.runtime.sendMessage({ action: "DY_SCOPE_RESULT", data: result }).catch((err) => {
            debugLog("listener:DY_SCOPE_RESULT_send_failed", { error: String(err) });
          });
        });
        return false;
      }
    );
    chrome.runtime.onMessage.addListener(
      (message, _sender, sendResponse) => {
        if (message.action !== "DY_SEARCH_EXECUTE") return false;
        const data = message.data;
        if (!isValidSearchExecuteMessage(data)) {
          debugLog("listener:invalid_search_execute", { message });
          return false;
        }
        debugLog("listener:DY_SEARCH_EXECUTE_received", {
          keyword: data.keyword,
          page_url: location.href
        });
        void runSearch(data).then((result) => {
          debugLog("runSearch:returning", {
            keyword: result.keyword,
            status: result.status,
            items_count: result.items.length
          });
          chrome.runtime.sendMessage({ action: "DY_SEARCH_RESULT", data: result }).catch((err) => {
            debugLog("listener:DY_SEARCH_RESULT_send_failed", { error: String(err) });
          });
        });
        return false;
      }
    );
    chrome.runtime.onMessage.addListener(
      (message, _sender, sendResponse) => {
        if (message.action !== "DY_HOT_EXECUTE") return false;
        const data = message.data;
        if (!isValidHotExecuteMessage(data)) {
          debugLog("listener:invalid_hot_execute", { message });
          return false;
        }
        debugLog("listener:DY_HOT_EXECUTE_received", {
          sentence_id: data.sentence_id,
          page_url: location.href
        });
        void runHot(data).then((result) => {
          debugLog("runHot:returning", {
            sentence_id: result.sentence_id,
            status: result.status,
            items_count: result.items.length
          });
          chrome.runtime.sendMessage({ action: "DY_HOT_RESULT", data: result }).catch((err) => {
            debugLog("listener:DY_HOT_RESULT_send_failed", { error: String(err) });
          });
        });
        return false;
      }
    );
    chrome.runtime.onMessage.addListener(
      (message, _sender, sendResponse) => {
        if (message.action !== "DY_FEED_EXECUTE") return false;
        const data = message.data;
        if (!isValidFeedExecuteMessage(data)) {
          debugLog("listener:invalid_feed_execute", { message });
          return false;
        }
        debugLog("listener:DY_FEED_EXECUTE_received", {
          page_url: location.href
        });
        void runFeed(data).then((result) => {
          debugLog("runFeed:returning", {
            status: result.status,
            items_count: result.items.length
          });
          chrome.runtime.sendMessage({ action: "DY_FEED_RESULT", data: result }).catch((err) => {
            debugLog("listener:DY_FEED_RESULT_send_failed", { error: String(err) });
          });
        });
        return false;
      }
    );
  }
  if (typeof chrome !== "undefined" && chrome.runtime) {
    registerDyScopeExecutor();
    console.debug("[OpenBiliClaw] dy content script registered (isolated world)");
  }
})();
//# sourceMappingURL=douyin.js.map
