"use strict";
(() => {
  // src/content/yt/task-executor.ts
  var KNOWN_SCOPES = [
    "yt_history",
    "yt_subscriptions",
    "yt_likes"
  ];
  function isKnownScope(s) {
    return KNOWN_SCOPES.includes(s);
  }
  function extractVideoItems(scope) {
    const items = [];
    const seen = /* @__PURE__ */ new Set();
    const renderers = Array.from(
      document.querySelectorAll(
        "ytd-video-renderer, ytd-playlist-video-renderer, ytd-rich-item-renderer"
      )
    );
    for (const el of renderers) {
      const anchor = el.querySelector(
        "a#thumbnail, a#video-title-link, a[id='thumbnail']"
      );
      const href = anchor?.href ?? anchor?.getAttribute("href") ?? "";
      const videoId = extractVideoId(href);
      const titleEl = el.querySelector("#video-title, #video-title-link") ?? el.querySelector("yt-formatted-string#video-title");
      const title = (titleEl?.textContent ?? "").trim();
      if (!title && !videoId) continue;
      const channelEl = el.querySelector(
        "#channel-name a, ytd-channel-name a, .ytd-channel-name a"
      ) ?? el.querySelector("#channel-name yt-formatted-string");
      const channel = (channelEl?.textContent ?? "").trim();
      const thumbImg = el.querySelector(
        "img#img, img.yt-thumbnail-view-model-wiz__image"
      );
      const cover_url = thumbImg?.src ?? "";
      const url = videoId ? `https://www.youtube.com/watch?v=${videoId}` : href || "";
      const key = videoId || title;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      items.push({ scope, video_id: videoId || void 0, title, channel, url, cover_url });
    }
    return items;
  }
  function extractChannelItems(scope) {
    const items = [];
    const seen = /* @__PURE__ */ new Set();
    const renderers = Array.from(
      document.querySelectorAll(
        "ytd-channel-renderer, ytd-grid-channel-renderer"
      )
    );
    for (const el of renderers) {
      const nameEl = el.querySelector("#channel-title, #channel-name, #name") ?? el.querySelector("yt-formatted-string#channel-title");
      const title = (nameEl?.textContent ?? "").trim();
      if (!title) continue;
      const linkEl = el.querySelector(
        "a#main-link, a#channel-title-link, a.channel-link"
      );
      const href = linkEl?.href ?? linkEl?.getAttribute("href") ?? "";
      const channelId = extractChannelId(href);
      const url = href || (channelId ? `https://www.youtube.com/channel/${channelId}` : "");
      const thumbImg = el.querySelector("img#img, yt-img-shadow img");
      const cover_url = thumbImg?.src ?? "";
      const key = channelId || title;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      items.push({ scope, channel_id: channelId || void 0, title, channel: title, url, cover_url });
    }
    return items;
  }
  async function scrollAndWait(rounds, waitMs) {
    for (let i = 0; i < rounds; i++) {
      window.scrollBy({ top: 3e3, behavior: "smooth" });
      await sleep(waitMs);
    }
  }
  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
  function extractVideoId(href) {
    const m = href.match(/[?&]v=([A-Za-z0-9_-]{11})/);
    return m ? m[1] : "";
  }
  function extractChannelId(href) {
    const m = href.match(/\/channel\/(UC[A-Za-z0-9_-]+)/);
    return m ? m[1] : "";
  }
  async function executeYtScope(msg) {
    const { task_id, scope, max_items_per_scope = 300, max_scroll_rounds = 10 } = msg;
    if (!isKnownScope(scope)) {
      return { task_id, scope, items: [], scope_count: 0, status: "failed", error: "unknown_scope" };
    }
    await sleep(1500);
    const scrollWaitMs = 1500;
    await scrollAndWait(max_scroll_rounds, scrollWaitMs);
    let items;
    if (scope === "yt_subscriptions") {
      items = extractChannelItems(scope);
    } else {
      items = extractVideoItems(scope);
    }
    const capped = items.slice(0, max_items_per_scope);
    return {
      task_id,
      scope,
      items: capped,
      scope_count: capped.length,
      status: capped.length > 0 ? "ok" : "empty",
      debug: { rendered_count: items.length, capped_count: capped.length, scroll_rounds: max_scroll_rounds }
    };
  }
  function installYtMessageListener() {
    chrome.runtime.onMessage.addListener(
      (message, _sender, sendResponse) => {
        if (message.action !== "YT_SCOPE_EXECUTE") return false;
        void executeYtScope(message.data).then((result) => {
          chrome.runtime.sendMessage({ action: "YT_SCOPE_RESULT", data: result });
          sendResponse({ ok: true });
        });
        return true;
      }
    );
  }

  // src/content/youtube.ts
  installYtMessageListener();
})();
//# sourceMappingURL=youtube.js.map
