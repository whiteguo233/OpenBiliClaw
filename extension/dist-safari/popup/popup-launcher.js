// popup-launcher.js
// Tiny launcher shown when the Safari toolbar action is clicked.
// - Shows a compact status row (background reachable? extension version)
// - "Open in new tab" button opens the full popup.html, which is the same
//   UI Chrome shows in its side panel and Firefox shows in its sidebar.
//
// This file is intentionally minimal and dependency-free; it must run in
// the popup context (regular extension page, not a content script).

(function () {
  "use strict";

  // chrome.* is the standard WebExtensions namespace on Safari, Chrome, and Edge.
  // Firefox also exposes browser.* — we prefer chrome.* here for consistency
  // with the rest of the codebase.
  var api = typeof chrome !== "undefined" ? chrome : null;

  function buildFullUiUrl() {
    var base = "popup/popup.html";
    if (api && api.runtime && typeof api.runtime.getURL === "function") {
      return api.runtime.getURL(base);
    }
    return base;
  }

  function openFullUi() {
    var url = buildFullUiUrl();
    if (api && api.tabs && typeof api.tabs.create === "function") {
      api.tabs.create({ url: url }, function () {
        // Close the popup window after the tab opens so focus moves naturally.
        if (typeof window !== "undefined" && typeof window.close === "function") {
          window.close();
        }
      });
      return;
    }
    // Fallback path — should never hit in a real extension context.
    window.open(url, "_blank");
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  /**
   * Update the status pill: replace its class suffix (pending|ok|err)
   * and rewrite its label. The pill structure (.pill > .dot + <span>)
   * is established in popup-launcher.html; we don't recreate the DOM
   * each call so the colored-dot pulse animation stays in sync.
   */
  function setPill(pillId, statusId, kind, label) {
    var pill = document.getElementById(pillId);
    if (pill) {
      // Strip any previous status class, keep the base ".pill" class.
      pill.className = "pill " + kind;
    }
    setText(statusId, label);
  }

  function reportVersion() {
    try {
      if (api && api.runtime && typeof api.runtime.getManifest === "function") {
        var manifest = api.runtime.getManifest();
        if (manifest && manifest.version) {
          setText("version", "v" + manifest.version);
          return;
        }
      }
    } catch (_) {
      // ignore
    }
    setText("version", "—");
  }

  function pingBackground() {
    if (!api || !api.runtime || typeof api.runtime.sendMessage !== "function") {
      setPill("bg-pill", "bg-status", "err", "不可用");
      return;
    }
    // We use a "ping" message; the service worker may or may not respond.
    // Either way, the absence of an error implies the runtime is reachable.
    //
    // Three terminal states:
    //   ok      — sendMessage round-tripped OR the 250ms timer expired
    //             without a thrown error (the SW is alive, just no listener
    //             for this message type, which is fine).
    //   err     — sendMessage threw synchronously (extension context is
    //             gone, runtime is unreachable).
    //   pending — initial state set in the HTML; replaced before we exit.
    var settled = false;
    var timer = setTimeout(function () {
      if (settled) return;
      settled = true;
      setPill("bg-pill", "bg-status", "ok", "运行中");
    }, 250);

    try {
      api.runtime.sendMessage({ type: "openbiliclaw/popup-launcher/ping" }, function () {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        // lastError just means no listener responded; the SW is still up.
        // Touch it so chrome doesn't log "unchecked runtime.lastError".
        var _ = api.runtime.lastError;
        setPill("bg-pill", "bg-status", "ok", "运行中");
      });
    } catch (_) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      setPill("bg-pill", "bg-status", "err", "未连接");
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("open-full");
    if (btn) btn.addEventListener("click", openFullUi);
    reportVersion();
    pingBackground();
  });
})();
