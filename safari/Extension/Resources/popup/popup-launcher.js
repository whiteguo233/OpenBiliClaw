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

  // Message-type constants come from the generated mirror at
  // popup-launcher-protocol.js, which itself is generated from
  // src/shared/popup-launcher-protocol.ts so the launcher and the
  // service worker can never drift. We fall back to inline literals
  // if the mirror somehow failed to load (defensive — in real
  // extension contexts the <script> tag has already run).
  var proto = (typeof window !== "undefined" && window.OpenBiliClawLauncher) || {};
  var PING_TYPE = proto.PING_LAUNCHER_TO_BG || "openbiliclaw/popup-launcher/ping";
  var QUERY_PENDING_TYPE = proto.QUERY_LAUNCHER_PENDING_STATUS || "openbiliclaw/popup-launcher/query-pending";
  var QUERY_WATCH_LATER_TYPE = proto.QUERY_LAUNCHER_WATCH_LATER_COUNT || "openbiliclaw/popup-launcher/query-watch-later-count";

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
      api.runtime.sendMessage({ type: PING_TYPE }, function () {
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

  /**
   * Ask the service worker whether the active tab is a B站 video the
   * backend has flagged as a YT repost. Renders the per-tab callout
   * row when so, and leaves it hidden otherwise.
   *
   * Errors are swallowed: this is decorative UI for the launcher, not
   * a correctness surface. A failed query just means the row stays
   * hidden, which is also what we'd show if the tab isn't a B站
   * video at all — so the user can't tell the difference between
   * "no data" and "nothing to show", which is fine here.
   */
  function queryRepostStatus() {
    if (!api || !api.runtime || typeof api.runtime.sendMessage !== "function") {
      return;
    }
    try {
      api.runtime.sendMessage({ type: QUERY_PENDING_TYPE }, function (reply) {
        // Touch lastError so chrome doesn't log "unchecked runtime.lastError".
        var _ = api.runtime && api.runtime.lastError;
        if (!reply || typeof reply !== "object") return;
        renderRepostCallout({
          isRepost: reply.isRepost === true,
          isPending: reply.isPending === true,
          ytUrl: typeof reply.ytUrl === "string" ? reply.ytUrl : "",
        });
      });
    } catch (_) {
      // SW unreachable — leave the callout hidden.
    }
  }

  function renderRepostCallout(status) {
    var container = document.getElementById("repost-callout");
    var known = document.getElementById("repost-known");
    var pending = document.getElementById("repost-pending");
    var jumpBtn = document.getElementById("repost-jump");
    if (!container || !known || !pending) return;

    // Hide everything by default; only flip the relevant pane on.
    container.hidden = true;
    known.hidden = true;
    pending.hidden = true;

    if (!status.isRepost) return;

    if (status.ytUrl) {
      container.hidden = false;
      known.hidden = false;
      if (jumpBtn) {
        // Replace any previous listener by reassigning onclick — the
        // launcher popup is a one-shot DOM, but be defensive.
        jumpBtn.onclick = function () {
          if (api && api.tabs && typeof api.tabs.update === "function") {
            // Navigate the active tab to the YT URL in-place, then
            // close the popup. tabs.create would orphan the B站 tab.
            api.tabs.update({ url: status.ytUrl }, function () {
              var _ = api.runtime && api.runtime.lastError;
              if (typeof window.close === "function") window.close();
            });
          } else {
            window.open(status.ytUrl, "_blank");
          }
        };
      }
      return;
    }

    if (status.isPending) {
      container.hidden = false;
      pending.hidden = false;
    }
  }

  /**
   * Fetch the user's 稍后再看 saved-count from the backend (via the
   * SW, which knows the configured endpoint) and render it into
   * the launcher's status card. Errors render "—" instead of "0"
   * so a backend outage doesn't look like "you have 0 bookmarks".
   */
  function queryWatchLaterCount() {
    if (!api || !api.runtime || typeof api.runtime.sendMessage !== "function") {
      setText("watch-later-count", "—");
      return;
    }
    try {
      api.runtime.sendMessage({ type: QUERY_WATCH_LATER_TYPE }, function (reply) {
        var _ = api.runtime && api.runtime.lastError;
        if (!reply || typeof reply !== "object" || reply.ok !== true) {
          setText("watch-later-count", "—");
          return;
        }
        var total = typeof reply.total === "number" ? reply.total : 0;
        setText("watch-later-count", String(total));
      });
    } catch (_) {
      setText("watch-later-count", "—");
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("open-full");
    if (btn) btn.addEventListener("click", openFullUi);
    reportVersion();
    pingBackground();
    queryRepostStatus();
    queryWatchLaterCount();
  });
})();
