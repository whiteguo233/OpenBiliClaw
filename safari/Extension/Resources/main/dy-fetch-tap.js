"use strict";
(() => {
  // src/main/dy-fetch-tap.ts
  function classifyDouyinResponseUrl(url) {
    if (!url) return null;
    const path = url.split("?", 1)[0] ?? "";
    if (path.includes("/aweme/v1/web/aweme/post/")) return "dy_post";
    if (path.includes("/aweme/v1/web/aweme/favorite/")) return "dy_collect";
    if (path.includes("/aweme/v1/web/aweme/collection/")) return "dy_collect";
    if (path.includes("/aweme/v1/web/aweme/like/")) return "dy_like";
    if (path.includes("/aweme/v1/web/user/follow/list/")) return "dy_follow";
    if (path.includes("/aweme/v1/web/user/following/list/")) return "dy_follow";
    return null;
  }
  function pickString(value) {
    return typeof value === "string" ? value : "";
  }
  function pickFirstUrl(coverField) {
    if (!coverField || typeof coverField !== "object") return "";
    const cover = coverField;
    if (!Array.isArray(cover.url_list)) return "";
    const first = cover.url_list.find((u) => typeof u === "string" && u);
    return typeof first === "string" ? first : "";
  }
  function pickAuthor(awemeAuthor) {
    if (!awemeAuthor || typeof awemeAuthor !== "object") return { nickname: "", sec_uid: "" };
    const a = awemeAuthor;
    return {
      nickname: pickString(a.nickname),
      sec_uid: pickString(a.sec_uid)
    };
  }
  function parseAwemeListResponse(json, scope) {
    if (!json || typeof json !== "object") return [];
    const root = json;
    if (!Array.isArray(root.aweme_list)) return [];
    const items = [];
    for (const raw of root.aweme_list) {
      if (!raw || typeof raw !== "object") continue;
      const aweme = raw;
      const awemeId = pickString(aweme.aweme_id);
      const title = pickString(aweme.desc) || pickString(aweme.preview_title);
      if (!awemeId && !title) continue;
      const author = pickAuthor(aweme.author);
      const coverUrl = pickFirstUrl(aweme.video?.cover);
      items.push({
        scope,
        aweme_id: awemeId,
        creator_sec_uid: "",
        url: awemeId ? `https://www.douyin.com/video/${awemeId}` : "",
        title,
        author: author.nickname,
        author_sec_uid: author.sec_uid,
        cover_url: coverUrl
      });
    }
    return items;
  }
  function parseUserFollowListResponse(json) {
    if (!json || typeof json !== "object") return [];
    const root = json;
    const list = Array.isArray(root.followings) ? root.followings : Array.isArray(root.follow_list) ? root.follow_list : null;
    if (!list) return [];
    const items = [];
    for (const raw of list) {
      if (!raw || typeof raw !== "object") continue;
      const creator = raw;
      const secUid = pickString(creator.sec_uid);
      if (!secUid) continue;
      const nickname = pickString(creator.nickname);
      const avatarUrl = pickFirstUrl(creator.avatar_thumb);
      items.push({
        scope: "dy_follow",
        aweme_id: "",
        creator_sec_uid: secUid,
        url: `https://www.douyin.com/user/${secUid}`,
        title: nickname,
        author: nickname,
        author_sec_uid: secUid,
        cover_url: avatarUrl
      });
    }
    return items;
  }
  function normalizeSearchAweme(raw, scope = "dy_search", meta = {}) {
    if (!raw || typeof raw !== "object") return null;
    const aweme = raw;
    const awemeId = pickString(aweme.aweme_id);
    const title = pickString(aweme.desc) || pickString(aweme.preview_title) || pickString(aweme.share_info?.share_title) || pickString(aweme.share_info?.share_desc);
    if (!awemeId && !title) return null;
    const author = pickAuthor(aweme.author);
    const item = {
      scope,
      aweme_id: awemeId,
      url: awemeId ? `https://www.douyin.com/video/${awemeId}` : "",
      title,
      author: author.nickname,
      author_sec_uid: author.sec_uid,
      cover_url: pickFirstUrl(aweme.video?.cover) || pickFirstUrl(aweme.video?.origin_cover) || pickFirstUrl(aweme.video?.animated_cover)
    };
    if (scope === "dy_hot") {
      item.hot_word = meta.word ?? "";
      item.sentence_id = meta.sentenceId ?? "";
      item.seed_aweme_id = meta.seedAwemeId ?? "";
    }
    return item;
  }
  function parseSearchAwemeResponse(json) {
    if (!json || typeof json !== "object") return [];
    const root = json;
    const rawRows = Array.isArray(root.aweme_list) ? root.aweme_list : Array.isArray(root.data) ? root.data : [];
    const items = [];
    const seen = /* @__PURE__ */ new Set();
    for (const row of rawRows) {
      if (!row || typeof row !== "object") continue;
      const record = row;
      const normalized = normalizeSearchAweme(record.aweme_info ?? record.item ?? record);
      if (!normalized) continue;
      const key = normalized.aweme_id || `${normalized.title}:${normalized.author}`;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      items.push(normalized);
    }
    return items;
  }
  function parseRelatedAwemeResponse(json, meta = {}) {
    if (!json || typeof json !== "object") return [];
    const root = json;
    if (!Array.isArray(root.aweme_list)) return [];
    const items = [];
    const seen = /* @__PURE__ */ new Set();
    for (const raw of root.aweme_list) {
      const normalized = normalizeSearchAweme(raw, "dy_hot", meta);
      if (!normalized) continue;
      const key = normalized.aweme_id || `${normalized.title}:${normalized.author}`;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      items.push(normalized);
    }
    return items;
  }
  function parseFeedAwemeResponse(json) {
    if (!json || typeof json !== "object") return [];
    const root = json;
    const rawRows = Array.isArray(root.aweme_list) ? root.aweme_list : Array.isArray(root.data) ? root.data : [];
    const items = [];
    const seen = /* @__PURE__ */ new Set();
    for (const row of rawRows) {
      if (!row || typeof row !== "object") continue;
      const record = row;
      const normalized = normalizeSearchAweme(
        record.aweme_info ?? record.item ?? record.aweme ?? record,
        "dy_feed"
      );
      if (!normalized) continue;
      if (!normalized.title && !normalized.author && !normalized.cover_url) continue;
      const key = normalized.aweme_id || `${normalized.title}:${normalized.author}`;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      items.push(normalized);
    }
    return items;
  }
  async function waitForDouyinSdk(target, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    const t = target;
    while (Date.now() < deadline) {
      if (t.byted_acrawler) return true;
      await new Promise((r) => setTimeout(r, 50));
    }
    return Boolean(t.byted_acrawler);
  }
  var URL_PROBE_TYPE = "OPENBILICLAW_DOUYIN_URL_PROBE";
  var SEC_UID_DETECTED_TYPE = "OPENBILICLAW_DOUYIN_SEC_UID";
  var SEARCH_TAP_MESSAGE_TYPE = "OPENBILICLAW_DOUYIN_SEARCH_PAGE";
  var _probeCount = 0;
  var _detectedSecUid = "";
  function probeUrl(transport, url) {
    if (!url) return;
    if (!url.includes("/aweme") && !url.includes("/user/")) return;
    if (_probeCount < 60) {
      _probeCount += 1;
      try {
        window.postMessage(
          { type: URL_PROBE_TYPE, transport, url, classified: classifyDouyinResponseUrl(url) },
          window.location.origin
        );
      } catch {
      }
    }
    const m = url.match(/[?&]sec_user_id=(MS4w[\w-]+)/);
    if (m && m[1] && m[1] !== _detectedSecUid) {
      _detectedSecUid = m[1];
      try {
        window.postMessage(
          { type: SEC_UID_DETECTED_TYPE, secUid: m[1] },
          window.location.origin
        );
      } catch {
      }
    }
  }
  function isSearchResponseUrl(url) {
    if (!url) return false;
    const path = url.split("?", 1)[0] ?? "";
    return path.includes("/aweme/v1/web/general/search/single/") || path.includes("/aweme/v1/web/search/item/");
  }
  function installFetchTap(target, postBack, postSearchBack) {
    const w = target;
    const originalFetch = w.fetch;
    const wrapped = async (input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      probeUrl("fetch", url);
      const resp = await originalFetch(input, init);
      const scope = classifyDouyinResponseUrl(url);
      if (scope) {
        try {
          const json = await resp.clone().json();
          const items = scope === "dy_follow" ? parseUserFollowListResponse(json) : parseAwemeListResponse(json, scope);
          if (items.length > 0) {
            postBack(items, scope);
          }
        } catch {
        }
      } else if (isSearchResponseUrl(url) && postSearchBack) {
        try {
          const json = await resp.clone().json();
          const items = parseSearchAwemeResponse(json);
          if (items.length > 0) {
            postSearchBack(items);
          }
        } catch {
        }
      }
      return resp;
    };
    w.fetch = wrapped;
    return () => {
      w.fetch = originalFetch;
    };
  }
  function installXhrTap(target, postBack, postSearchBack) {
    const proto = target.XMLHttpRequest.prototype;
    const originalOpen = proto.open;
    const wrappedOpen = function wrappedOpen2(method, url, async, user, password) {
      const urlString = typeof url === "string" ? url : url.toString();
      this.__obcUrl = urlString;
      probeUrl("xhr", urlString);
      this.addEventListener("readystatechange", () => {
        if (this.readyState !== 4) return;
        const u = this.__obcUrl ?? urlString;
        const scope = classifyDouyinResponseUrl(u);
        if (!scope && !isSearchResponseUrl(u)) return;
        try {
          const text = this.responseText;
          if (!text) return;
          const json = JSON.parse(text);
          if (scope) {
            const items = scope === "dy_follow" ? parseUserFollowListResponse(json) : parseAwemeListResponse(json, scope);
            if (items.length > 0) postBack(items, scope);
            return;
          }
          const searchItems = parseSearchAwemeResponse(json);
          if (searchItems.length > 0 && postSearchBack) postSearchBack(searchItems);
        } catch {
        }
      });
      return originalOpen.call(this, method, url, async ?? true, user, password);
    };
    proto.open = wrappedOpen;
    return () => {
      proto.open = originalOpen;
    };
  }
  var FETCH_TAP_MESSAGE_TYPE = "OPENBILICLAW_DOUYIN_AWEME_PAGE";
  var FETCH_TAP_INSTALL_TYPE = "OPENBILICLAW_DOUYIN_FETCH_TAP_INSTALL";
  function replayInstallStatusPing(status) {
    const fire = () => {
      window.postMessage({ type: FETCH_TAP_INSTALL_TYPE, status }, window.location.origin);
    };
    fire();
    setTimeout(fire, 500);
    setTimeout(fire, 1e3);
  }
  var API_REQUEST_TYPE = "OPENBILICLAW_DOUYIN_API_REQUEST";
  var API_RESPONSE_TYPE = "OPENBILICLAW_DOUYIN_API_RESPONSE";
  var SEARCH_API_REQUEST_TYPE = "OPENBILICLAW_DOUYIN_SEARCH_API_REQUEST";
  var SEARCH_API_RESPONSE_TYPE = "OPENBILICLAW_DOUYIN_SEARCH_API_RESPONSE";
  var HOT_API_REQUEST_TYPE = "OPENBILICLAW_DOUYIN_HOT_API_REQUEST";
  var HOT_API_RESPONSE_TYPE = "OPENBILICLAW_DOUYIN_HOT_API_RESPONSE";
  var FEED_API_REQUEST_TYPE = "OPENBILICLAW_DOUYIN_FEED_API_REQUEST";
  var FEED_API_RESPONSE_TYPE = "OPENBILICLAW_DOUYIN_FEED_API_RESPONSE";
  var SCOPE_ENDPOINT = {
    dy_post: "/aweme/v1/web/aweme/post/",
    dy_collect: "/aweme/v1/web/aweme/favorite/",
    dy_like: "/aweme/v1/web/aweme/like/",
    dy_follow: "/aweme/v1/web/user/follow/list/"
  };
  function buildScopeApiUrl(scope, secUid, cursor) {
    const params = new URLSearchParams({
      device_platform: "webapp",
      aid: "6383",
      channel: "channel_pc_web",
      pc_client_type: "1",
      sec_user_id: secUid,
      count: scope === "dy_follow" ? "20" : "18",
      publish_video_strategy_type: "2",
      update_version_code: "170400",
      version_code: "170400",
      version_name: "17.4.0",
      cookie_enabled: "true"
    });
    if (scope === "dy_follow") {
      params.set("max_time", String(cursor));
      params.set("min_time", "0");
      params.set("with_fstatus", "1");
      params.set("source_type", "1");
    } else {
      params.set("max_cursor", String(cursor));
      params.set("min_cursor", "0");
      params.set("whale_cut_token", "");
      params.set("cut_version", "1");
    }
    return `${SCOPE_ENDPOINT[scope]}?${params.toString()}`;
  }
  async function harvestScopeViaApi(target, scope, secUid, maxItems) {
    const w = target;
    const items = [];
    const seen = /* @__PURE__ */ new Set();
    let cursor = 0;
    let pages = 0;
    const cap = Math.max(0, Math.floor(maxItems));
    const MAX_PAGES = 50;
    for (let page = 0; page < MAX_PAGES && items.length < cap; page += 1) {
      const url = buildScopeApiUrl(scope, secUid, cursor);
      let json;
      try {
        const resp = await w.fetch(url, { credentials: "include" });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        json = await resp.json();
      } catch (err) {
        if (page === 0) throw err;
        break;
      }
      pages += 1;
      const batch = scope === "dy_follow" ? parseUserFollowListResponse(json) : parseAwemeListResponse(json, scope);
      for (const item of batch) {
        const key = scope === "dy_follow" ? item.creator_sec_uid : item.aweme_id;
        if (!key || seen.has(key)) continue;
        seen.add(key);
        items.push(item);
        if (items.length >= cap) break;
      }
      const root = json;
      const hasMore = Boolean(root.has_more);
      if (!hasMore) break;
      const nextCursor = scope === "dy_follow" ? typeof root.min_time === "number" ? root.min_time : 0 : typeof root.max_cursor === "number" ? root.max_cursor : 0;
      if (!nextCursor || nextCursor === cursor) break;
      cursor = nextCursor;
      await new Promise((r) => setTimeout(r, 300));
    }
    return { items, pages_fetched: pages };
  }
  function pickCookieValue(cookieHeader, name) {
    const prefix = `${name}=`;
    for (const part of cookieHeader.split(";")) {
      const trimmed = part.trim();
      if (trimmed.startsWith(prefix)) return trimmed.slice(prefix.length);
    }
    return "";
  }
  function parseChromeVersion(userAgent) {
    const match = userAgent.match(/(?:Chrome|Chromium)\/([\d.]+)/);
    return match?.[1] ?? "131.0.0.0";
  }
  function buildSearchApiUrl(target, path, keyword, offset, count) {
    const nav = target.navigator;
    const chromeVersion = parseChromeVersion(nav?.userAgent ?? "");
    const platform = nav?.platform || "Win32";
    const isMac = /Mac/i.test(platform);
    const params = new URLSearchParams({
      device_platform: "webapp",
      aid: "6383",
      channel: "channel_pc_web",
      pc_client_type: "1",
      version_code: "290100",
      version_name: "29.1.0",
      cookie_enabled: "true",
      screen_width: String(target.screen?.width ?? 1920),
      screen_height: String(target.screen?.height ?? 1080),
      browser_language: nav?.language || "zh-CN",
      browser_platform: platform,
      browser_name: "Chrome",
      browser_version: chromeVersion,
      browser_online: String(nav?.onLine ?? true),
      engine_name: "Blink",
      engine_version: chromeVersion,
      os_name: isMac ? "Mac OS" : "Windows",
      os_version: isMac ? "10.15.7" : "10",
      platform: "PC",
      msToken: pickCookieValue(target.document?.cookie ?? "", "msToken"),
      keyword,
      search_source: "normal_search",
      query_correct_type: "1",
      is_filter_search: "0",
      offset: String(offset),
      count: String(count)
    });
    if (path.includes("/general/search/single/")) {
      params.set("search_channel", "aweme_video_web");
    }
    return `${target.location.origin}${path}?${params.toString()}`;
  }
  function applyDouyinApiSignature(target, url) {
    const acrawler = target.byted_acrawler;
    if (typeof acrawler?.frontierSign !== "function") return url;
    let signed;
    try {
      signed = acrawler.frontierSign({ url });
    } catch {
      return url;
    }
    if (!signed) return url;
    if (typeof signed === "string") {
      if (/^https?:\/\//.test(signed) || signed.startsWith("/")) return signed;
      const parsed2 = new URL(url);
      if (signed.includes("=")) {
        const params = new URLSearchParams(signed.replace(/^[?&]/, ""));
        params.forEach((value, key) => parsed2.searchParams.set(key, value));
      } else {
        parsed2.searchParams.set("X-Bogus", signed);
      }
      return parsed2.toString();
    }
    if (typeof signed !== "object") return url;
    const result = signed;
    const signedUrl = pickString(result.url) || pickString(result.signed_url);
    if (signedUrl) return signedUrl;
    const parsed = new URL(url);
    const xBogus = pickString(result["X-Bogus"]) || pickString(result["x-bogus"]);
    const aBogus = pickString(result.a_bogus) || pickString(result["a-bogus"]);
    if (xBogus) parsed.searchParams.set("X-Bogus", xBogus);
    if (aBogus) parsed.searchParams.set("a_bogus", aBogus);
    return parsed.toString();
  }
  async function harvestSearchViaApi(target, keyword, maxItems) {
    const w = target;
    const items = [];
    const seen = /* @__PURE__ */ new Set();
    const cap = Math.max(0, Math.floor(maxItems));
    const pageSize = Math.min(20, Math.max(1, cap || 1));
    const paths = [
      "/aweme/v1/web/general/search/single/",
      "/aweme/v1/web/search/item/"
    ];
    let pages = 0;
    for (const path of paths) {
      let offset = 0;
      for (let page = 0; page < 5 && items.length < cap; page += 1) {
        const url = applyDouyinApiSignature(
          target,
          buildSearchApiUrl(target, path, keyword, offset, pageSize)
        );
        let json;
        try {
          const resp = await w.fetch(url, { credentials: "include" });
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          json = await resp.json();
        } catch (err) {
          if (page === 0 && path === paths[0]) throw err;
          break;
        }
        pages += 1;
        for (const item of parseSearchAwemeResponse(json)) {
          const key = item.aweme_id || `${item.title}:${item.author}`;
          if (!key || seen.has(key)) continue;
          seen.add(key);
          items.push(item);
          if (items.length >= cap) break;
        }
        const root = json;
        const hasMore = Boolean(root.has_more);
        const nextOffset = Number(root.cursor ?? offset + pageSize);
        if (!hasMore || !Number.isFinite(nextOffset) || nextOffset === offset) break;
        offset = nextOffset;
        await new Promise((r) => setTimeout(r, 300));
      }
      if (items.length > 0) break;
    }
    return { items, pages_fetched: pages };
  }
  function buildRelatedApiUrl(target, seedAwemeId, count) {
    const nav = target.navigator;
    const chromeVersion = parseChromeVersion(nav?.userAgent ?? "");
    const platform = nav?.platform || "Win32";
    const isMac = /Mac/i.test(platform);
    const params = new URLSearchParams({
      device_platform: "webapp",
      aid: "6383",
      channel: "channel_pc_web",
      pc_client_type: "1",
      version_code: "290100",
      version_name: "29.1.0",
      cookie_enabled: "true",
      screen_width: String(target.screen?.width ?? 1920),
      screen_height: String(target.screen?.height ?? 1080),
      browser_language: nav?.language || "zh-CN",
      browser_platform: platform,
      browser_name: "Chrome",
      browser_version: chromeVersion,
      browser_online: String(nav?.onLine ?? true),
      engine_name: "Blink",
      engine_version: chromeVersion,
      os_name: isMac ? "Mac OS" : "Windows",
      os_version: isMac ? "10.15.7" : "10",
      platform: "PC",
      msToken: pickCookieValue(target.document?.cookie ?? "", "msToken"),
      aweme_id: seedAwemeId,
      count: String(count),
      filterGids: ""
    });
    return `${target.location.origin}/aweme/v1/web/aweme/related/?${params.toString()}`;
  }
  function buildFeedApiUrl(target, count, refreshIndex) {
    const nav = target.navigator;
    const chromeVersion = parseChromeVersion(nav?.userAgent ?? "");
    const platform = nav?.platform || "Win32";
    const isMac = /Mac/i.test(platform);
    const params = new URLSearchParams({
      device_platform: "webapp",
      aid: "6383",
      channel: "channel_pc_web",
      pc_client_type: "1",
      version_code: "290100",
      version_name: "29.1.0",
      cookie_enabled: "true",
      screen_width: String(target.screen?.width ?? 1920),
      screen_height: String(target.screen?.height ?? 1080),
      browser_language: nav?.language || "zh-CN",
      browser_platform: platform,
      browser_name: "Chrome",
      browser_version: chromeVersion,
      browser_online: String(nav?.onLine ?? true),
      engine_name: "Blink",
      engine_version: chromeVersion,
      os_name: isMac ? "Mac OS" : "Windows",
      os_version: isMac ? "10.15.7" : "10",
      platform: "PC",
      msToken: pickCookieValue(target.document?.cookie ?? "", "msToken"),
      count: String(count),
      tag_id: "",
      share_aweme_id: "",
      live_insert_type: "",
      refresh_index: String(refreshIndex),
      video_type_select: "1",
      aweme_pc_rec_raw_data: '{"is_client":"false"}',
      globalwid: "",
      pull_type: "",
      min_window: "",
      free_right: "",
      ug_source: "",
      creative_id: ""
    });
    return `${target.location.origin}/aweme/v1/web/tab/feed/?${params.toString()}`;
  }
  async function harvestHotRelatedViaApi(target, seedAwemeId, maxItems, meta) {
    const w = target;
    const cap = Math.max(0, Math.floor(maxItems));
    if (!seedAwemeId || cap <= 0) return { items: [], pages_fetched: 0 };
    const url = applyDouyinApiSignature(
      target,
      buildRelatedApiUrl(target, seedAwemeId, Math.min(20, cap))
    );
    const resp = await w.fetch(url, { credentials: "include" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const json = await resp.json();
    return {
      items: parseRelatedAwemeResponse(json, meta).slice(0, cap),
      pages_fetched: 1
    };
  }
  async function harvestFeedViaApi(target, maxItems) {
    const w = target;
    const cap = Math.max(0, Math.floor(maxItems));
    if (cap <= 0) return { items: [], pages_fetched: 0 };
    const requestCount = Math.min(20, Math.max(10, cap * 2));
    const url = applyDouyinApiSignature(target, buildFeedApiUrl(target, requestCount, 1));
    const resp = await w.fetch(url, { credentials: "include" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const json = await resp.json();
    return {
      items: parseFeedAwemeResponse(json).slice(0, cap),
      pages_fetched: 1
    };
  }
  function installApiHarvester(target) {
    target.addEventListener("message", (event) => {
      const data = event?.data ?? null;
      if (!data || typeof data !== "object") return;
      if (data.type === SEARCH_API_REQUEST_TYPE) {
        const requestId2 = String(data.requestId ?? "");
        const keyword = String(data.keyword ?? "").trim();
        const maxItems2 = Number(data.maxItems ?? 0);
        if (!requestId2 || !keyword) return;
        void (async () => {
          try {
            const result = await harvestSearchViaApi(target, keyword, maxItems2);
            target.postMessage(
              {
                type: SEARCH_API_RESPONSE_TYPE,
                requestId: requestId2,
                items: result.items,
                pages_fetched: result.pages_fetched
              },
              target.location.origin
            );
          } catch (err) {
            target.postMessage(
              {
                type: SEARCH_API_RESPONSE_TYPE,
                requestId: requestId2,
                items: [],
                pages_fetched: 0,
                error: String(err instanceof Error ? err.message : err)
              },
              target.location.origin
            );
          }
        })();
        return;
      }
      if (data.type === HOT_API_REQUEST_TYPE) {
        const requestId2 = String(data.requestId ?? "");
        const seedAwemeId = String(data.seedAwemeId ?? "").trim();
        const maxItems2 = Number(data.maxItems ?? 0);
        const word = String(data.word ?? "");
        const sentenceId = String(data.sentenceId ?? "");
        if (!requestId2 || !seedAwemeId) return;
        void (async () => {
          try {
            const result = await harvestHotRelatedViaApi(target, seedAwemeId, maxItems2, {
              word,
              sentenceId,
              seedAwemeId
            });
            target.postMessage(
              {
                type: HOT_API_RESPONSE_TYPE,
                requestId: requestId2,
                items: result.items,
                pages_fetched: result.pages_fetched
              },
              target.location.origin
            );
          } catch (err) {
            target.postMessage(
              {
                type: HOT_API_RESPONSE_TYPE,
                requestId: requestId2,
                items: [],
                pages_fetched: 0,
                error: String(err instanceof Error ? err.message : err)
              },
              target.location.origin
            );
          }
        })();
        return;
      }
      if (data.type === FEED_API_REQUEST_TYPE) {
        const requestId2 = String(data.requestId ?? "");
        const maxItems2 = Number(data.maxItems ?? 0);
        if (!requestId2) return;
        void (async () => {
          try {
            const result = await harvestFeedViaApi(target, maxItems2);
            target.postMessage(
              {
                type: FEED_API_RESPONSE_TYPE,
                requestId: requestId2,
                items: result.items,
                pages_fetched: result.pages_fetched
              },
              target.location.origin
            );
          } catch (err) {
            target.postMessage(
              {
                type: FEED_API_RESPONSE_TYPE,
                requestId: requestId2,
                items: [],
                pages_fetched: 0,
                error: String(err instanceof Error ? err.message : err)
              },
              target.location.origin
            );
          }
        })();
        return;
      }
      if (data.type !== API_REQUEST_TYPE) return;
      const requestId = String(data.requestId ?? "");
      const scope = data.scope;
      const secUid = String(data.secUid ?? "");
      const maxItems = Number(data.maxItems ?? 0);
      if (!requestId || !scope || !secUid) return;
      void (async () => {
        try {
          const result = await harvestScopeViaApi(target, scope, secUid, maxItems);
          target.postMessage(
            {
              type: API_RESPONSE_TYPE,
              requestId,
              items: result.items,
              pages_fetched: result.pages_fetched
            },
            target.location.origin
          );
        } catch (err) {
          target.postMessage(
            {
              type: API_RESPONSE_TYPE,
              requestId,
              items: [],
              pages_fetched: 0,
              error: String(err instanceof Error ? err.message : err)
            },
            target.location.origin
          );
        }
      })();
    });
  }
  if (typeof window !== "undefined" && typeof document !== "undefined") {
    void waitForDouyinSdk(window, 15e3).then((ready) => {
      if (!ready) {
        replayInstallStatusPing("skipped_no_sdk");
        console.debug("[OpenBiliClaw] dy fetch-tap skipped: SDK not detected");
        return;
      }
      const postItems = (items, scope) => {
        window.postMessage(
          { type: FETCH_TAP_MESSAGE_TYPE, scope, items },
          window.location.origin
        );
      };
      const postSearchItems = (items) => {
        window.postMessage(
          { type: SEARCH_TAP_MESSAGE_TYPE, items },
          window.location.origin
        );
      };
      installFetchTap(window, postItems, postSearchItems);
      installXhrTap(window, postItems, postSearchItems);
      installApiHarvester(window);
      replayInstallStatusPing("installed");
      console.debug("[OpenBiliClaw] dy fetch-tap + API harvester installed (MAIN world)");
    });
  }
})();
//# sourceMappingURL=dy-fetch-tap.js.map
