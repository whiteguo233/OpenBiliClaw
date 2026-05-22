"use strict";
(() => {
  // src/main/xhs-state-bridge.ts
  var POST_MESSAGE_SOURCE = "obc-xhs-state";
  var STATE_WHITELIST = /* @__PURE__ */ new Set([
    "user",
    "saved",
    "collect",
    "collections",
    "liked",
    "likes",
    "history",
    "footprint",
    "browseHistory",
    "browsingHistory"
  ]);
  var MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024;
  function isVueRef(value) {
    if (value === null || typeof value !== "object") return false;
    const obj = value;
    return obj.__v_isRef === true || "_rawValue" in obj;
  }
  function safeJsonClone(value, seen) {
    if (value === null || value === void 0) return value;
    const t = typeof value;
    if (t === "function" || t === "symbol") return void 0;
    if (t !== "object") return value;
    if (isVueRef(value)) {
      const r = value;
      if ("_rawValue" in r) return safeJsonClone(r._rawValue, seen);
      if ("_value" in r) return safeJsonClone(r._value, seen);
    }
    const tracker = seen ?? /* @__PURE__ */ new WeakSet();
    if (tracker.has(value)) return void 0;
    tracker.add(value);
    if (Array.isArray(value)) {
      return value.map((item) => safeJsonClone(item, tracker));
    }
    const obj = value;
    const out = {};
    for (const key of Object.keys(obj)) {
      if (key.startsWith("__v_")) continue;
      if (key === "dep" || key === "deps" || key === "sub" || key === "subs") continue;
      let val;
      try {
        val = obj[key];
      } catch {
        continue;
      }
      const cloned = safeJsonClone(val, tracker);
      if (cloned !== void 0) out[key] = cloned;
    }
    return out;
  }
  function buildStateSnapshot(rawState) {
    if (rawState === null || typeof rawState !== "object") return null;
    const out = {};
    for (const key of Object.keys(rawState)) {
      if (!STATE_WHITELIST.has(key)) continue;
      const cloned = safeJsonClone(rawState[key]);
      if (cloned !== void 0) out[key] = cloned;
    }
    return Object.keys(out).length > 0 ? out : null;
  }
  function buildMinimalSnapshot(rawState) {
    if (rawState === null || typeof rawState !== "object") return null;
    const user = rawState.user;
    if (!user || typeof user !== "object") return null;
    const userObj = user;
    const userOut = {};
    for (const key of ["loggedIn", "userInfo", "userPageData"]) {
      const cloned = safeJsonClone(userObj[key]);
      if (cloned !== void 0) userOut[key] = cloned;
    }
    return Object.keys(userOut).length > 0 ? { user: userOut } : null;
  }
  function approximateByteSize(value) {
    try {
      return JSON.stringify(value).length;
    } catch {
      return Number.POSITIVE_INFINITY;
    }
  }
  var lastSnapshotJson = "";
  function emitOnce() {
    const win = window;
    const raw = win.__INITIAL_STATE__;
    if (!raw) return;
    let snapshot = buildStateSnapshot(raw);
    if (snapshot === null) return;
    if (approximateByteSize(snapshot) > MAX_SNAPSHOT_BYTES) {
      const minimal = buildMinimalSnapshot(raw);
      if (minimal === null) return;
      snapshot = minimal;
    }
    const json = JSON.stringify(snapshot);
    if (json === lastSnapshotJson) return;
    lastSnapshotJson = json;
    try {
      window.postMessage(
        { source: POST_MESSAGE_SOURCE, state: snapshot },
        "*"
      );
    } catch {
    }
  }
  function startPolling() {
    let attempts = 0;
    const tick = () => {
      attempts += 1;
      emitOnce();
      if (attempts >= 60) return;
      window.setTimeout(tick, 250);
    };
    tick();
  }
  if (typeof window !== "undefined") {
    startPolling();
    window.addEventListener("popstate", emitOnce);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") emitOnce();
    });
    window.addEventListener("click", emitOnce, { passive: true });
    console.debug("[OpenBiliClaw] xhs state bridge installed (MAIN world)");
  }
})();
//# sourceMappingURL=xhs-state-bridge.js.map
