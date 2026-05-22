"use strict";
(() => {
  // src/main/xhs-token-sniffer.ts
  var POST_MESSAGE_SOURCE = "obc-xhs-sniffer";
  var NOTE_ID_KEYS = ["note_id", "noteId", "id"];
  var TOKEN_KEYS = ["xsec_token", "xsecToken"];
  function extractTokenPairs(payload) {
    const out = [];
    const seen = /* @__PURE__ */ new Set();
    function pushIfNew(pair) {
      if (!pair.note_id || !pair.xsec_token) return;
      const key = `${pair.note_id}|${pair.xsec_token}`;
      if (seen.has(key)) return;
      seen.add(key);
      out.push(pair);
    }
    function walk(node) {
      if (node === null || typeof node !== "object") return;
      if (Array.isArray(node)) {
        for (const child of node) walk(child);
        return;
      }
      const obj = node;
      let note_id = "";
      let xsec_token = "";
      for (const k of NOTE_ID_KEYS) {
        const v = obj[k];
        if (typeof v === "string" && /^[0-9a-f]{24}$/i.test(v)) {
          note_id = v;
          break;
        }
      }
      for (const k of TOKEN_KEYS) {
        const v = obj[k];
        if (typeof v === "string" && v.length > 0) {
          xsec_token = v;
          break;
        }
      }
      pushIfNew({ note_id, xsec_token });
      for (const value of Object.values(obj)) walk(value);
    }
    walk(payload);
    return out;
  }
  function emit(pairs) {
    if (pairs.length === 0) return;
    window.postMessage({ source: POST_MESSAGE_SOURCE, pairs }, "*");
  }
  function isXhsApiUrl(url) {
    return url.includes("/api/sns/web/") || url.includes("edith.xiaohongshu.com");
  }
  async function parseResponseSafely(res) {
    try {
      const clone = res.clone();
      const text = await clone.text();
      if (!text) return null;
      return JSON.parse(text);
    } catch {
      return null;
    }
  }
  function installSniffer() {
    const originalFetch = window.fetch.bind(window);
    window.fetch = async function wrappedFetch(input, init) {
      const response = await originalFetch(input, init);
      try {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        if (url && isXhsApiUrl(url)) {
          void parseResponseSafely(response).then((json) => {
            if (json !== null) emit(extractTokenPairs(json));
          });
        }
      } catch {
      }
      return response;
    };
    const XhrProto = XMLHttpRequest.prototype;
    const originalOpen = XhrProto.open;
    const originalSend = XhrProto.send;
    XhrProto.open = function patchedOpen(method, url, async, user, password) {
      this.__obcXhsUrl = typeof url === "string" ? url : url.href;
      return originalOpen.call(
        this,
        method,
        url,
        async ?? true,
        user ?? null,
        password ?? null
      );
    };
    XhrProto.send = function patchedSend(body) {
      const url = this.__obcXhsUrl ?? "";
      if (url && isXhsApiUrl(url)) {
        this.addEventListener("load", () => {
          try {
            if (this.responseType === "" || this.responseType === "text") {
              const text = this.responseText;
              if (text) {
                const json = JSON.parse(text);
                emit(extractTokenPairs(json));
              }
            } else if (this.responseType === "json") {
              emit(extractTokenPairs(this.response));
            }
          } catch {
          }
        });
      }
      return originalSend.call(this, body ?? null);
    };
    console.debug("[OpenBiliClaw] xhs token sniffer installed (MAIN world)");
  }
  if (typeof window !== "undefined" && typeof XMLHttpRequest !== "undefined") {
    installSniffer();
  }
})();
//# sourceMappingURL=xhs-token-sniffer.js.map
