import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("mobile web exposes watch-later API and tab entry", async () => {
  globalThis.location = { protocol: "http:", host: "127.0.0.1:8420" };
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { items: [{ bvid: "BV1MOBILE" }], total: 1 };
      },
    };
  };

  const api = await import("../../src/openbiliclaw/web/js/api.js?watch-later-api");

  assert.equal(typeof api.fetchWatchLater, "function");
  assert.deepEqual(await api.fetchWatchLater(20, 40), {
    items: [{ bvid: "BV1MOBILE" }],
    total: 1,
  });
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/watch-later?limit=20&offset=40");

  const appJs = readFileSync(resolve("../src/openbiliclaw/web/js/app.js"), "utf8");
  assert.match(appJs, /initWatchLaterView/);
  assert.match(appJs, /id:\s*"watchLater"/);
  assert.match(appJs, /label:\s*"稍后"/);
});

test("mobile recommend delight tray has a watch-later star action", () => {
  const recommendJs = readFileSync(resolve("../src/openbiliclaw/web/js/views/recommend.js"), "utf8");

  assert.match(recommendJs, /action:\s*"watch-later"/);
  assert.match(recommendJs, /addToWatchLater\(d\.bvid\)/);
});

test("desktop web exposes watch-later page, badge, and delight star", () => {
  const desktopHtml = readFileSync(resolve("../src/openbiliclaw/web/desktop/index.html"), "utf8");
  const desktopJs = readFileSync(
    resolve("../src/openbiliclaw/web/desktop/assets/js/app.js"),
    "utf8",
  );

  assert.match(desktopHtml, /id="watchLaterBtn"/);
  assert.match(desktopHtml, /id="watchLaterCountBadge"/);
  assert.match(desktopHtml, /id="watchLaterPage"/);
  assert.match(desktopHtml, /data-delight="watch-later"/);
  assert.match(desktopJs, /watchLaterPage/);
  assert.match(desktopJs, /refreshWatchLater/);
  assert.match(desktopJs, /watchLaterStatus/);
  assert.match(desktopJs, /syncWatchLaterButtons/);
});

test("extension delight banner has a watch-later star action", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /delightWatchLaterButton/);
  assert.match(popupJs, /toggleWatchLaterSaved\(delight\.bvid\)/);
  assert.match(popupJs, /bindWatchLaterToggle\(btn,\s*delight\.bvid\)/);
});

test("extension popup exposes a watch-later tab and list like web surfaces", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupHtml, /id="tabWatchLater"/);
  assert.match(popupHtml, /aria-controls="viewWatchLater"/);
  assert.match(popupHtml, /id="viewWatchLater"/);
  assert.match(popupHtml, /id="watchLaterList"/);
  assert.match(popupHtml, /id="watchLaterEmpty"/);
  assert.match(popupJs, /fetchWatchLater/);
  assert.match(popupJs, /function loadWatchLater/);
  assert.match(popupJs, /function buildWatchLaterCard/);
  // Removal goes through the shared optimistic binder (remove first,
  // restore + 重试 on failure) instead of an inline await-then-remove.
  assert.match(popupJs, /requestRemove:\s*removeFromWatchLater/);
});
