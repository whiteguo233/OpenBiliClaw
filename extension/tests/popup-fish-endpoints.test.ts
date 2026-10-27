import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { requestJson } from "../popup/popup-api.js";

// Exercise real panel handlers through the popup URL/auth/request helper.
test("fish panel loads, saves and polls beneath exactly one API prefix", async () => {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: "", textContent: "", style: {}, checked: false, disabled: false,
      handlers: {},
      addEventListener(type, handler) { this.handlers[type] = handler; },
    });
    return elements.get(id);
  };
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ path: new URL(url).pathname, method: options.method || "GET" });
    return { ok: true, async json() {
      return { config: {}, account: {}, state: {}, ok: true,
        status: "logged_in", qrcode_key: "test-key", qrcode_image: "data:image/svg+xml," };
    } };
  };
  try {
    const source = readFileSync(resolve("popup/popup-fish.js"), "utf8")
      .replace('import { requestJson } from "./popup-api.js";', "");
    runInNewContext(source, {
      document: { getElementById: element }, requestJson, encodeURIComponent,
      setTimeout: (callback) => setTimeout(callback, 0),
    });
    for (const id of ["settingsTabFish", "fishSaveConfigBtn", "fishSaveAccountBtn",
      "fishQrLoginBtn", "fishClearAccountBtn", "fishPreviewBtn"]) {
      element(id).handlers.click();
      for (let tick = 0; tick < 20; tick++) await new Promise(resolve => setTimeout(resolve, 1));
    }
    for (const call of calls) assert.match(call.path, /^\/api\/fish\//);
    for (const [path, method] of [
      ["config", "GET"], ["status", "GET"], ["config", "PUT"],
      ["account", "POST"], ["account/qrcode", "POST"],
      ["account/qrcode/poll", "GET"], ["account", "DELETE"], ["run", "POST"],
    ]) assert.ok(calls.some(call => call.path === `/api/fish/${path}` && call.method === method), `${method} ${path}`);
  } finally { globalThis.fetch = originalFetch; }
});
