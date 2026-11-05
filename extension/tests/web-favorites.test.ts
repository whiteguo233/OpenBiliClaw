import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("mobile web exposes favorites API and tab entry", async () => {
  globalThis.location = new URL("http://127.0.0.1:8420/m/");
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { items: [{ bvid: "BV1FAVMOBILE" }], total: 1 };
      },
    };
  };

  const api = await import("../../src/openbiliclaw/web/js/api.js?favorites-api");

  assert.equal(typeof api.fetchFavorites, "function");
  assert.equal(typeof api.addToFavorite, "function");
  assert.equal(typeof api.removeFromFavorite, "function");
  assert.equal(typeof api.favoriteStatus, "function");
  assert.deepEqual(await api.fetchFavorites(20, 40), {
    items: [{ bvid: "BV1FAVMOBILE" }],
    total: 1,
  });
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/favorites?limit=20&offset=40");

  const appJs = readFileSync(resolve("../src/openbiliclaw/web/js/app.js"), "utf8");
  const libraryJs = readFileSync(resolve("../src/openbiliclaw/web/js/views/library.js"), "utf8");
  assert.match(appJs, /id:\s*"library"/);
  assert.match(appJs, /label:\s*"内容库"/);
  assert.match(appJs, /\["watchLater", "favorites", "history"\]\.includes\(id\)/);
  assert.match(libraryJs, /initFavoritesView/);
  assert.match(libraryJs, /id:\s*"favorites"/);
  assert.match(libraryJs, /label:\s*"收藏"/);
});

test("mobile recommend delight tray has a favorite star action", () => {
  const recommendJs = readFileSync(
    resolve("../src/openbiliclaw/web/js/views/recommend.js"),
    "utf8",
  );

  assert.match(recommendJs, /action:\s*"favorite"/);
  assert.match(recommendJs, /toggleSavedLocally\("favorite", savedItem\)/);
});

test("mobile recommend cards have a favorite star toggle", () => {
  const recommendJs = readFileSync(
    resolve("../src/openbiliclaw/web/js/views/recommend.js"),
    "utf8",
  );

  assert.match(recommendJs, /toggleSavedLocally\("favorite", savedItem\)/);
  assert.match(recommendJs, /hydrateSavedLocally\("favorite", savedItem/);
});

test("desktop web exposes favorites page, badge, and delight star", () => {
  const desktopHtml = readFileSync(
    resolve("../src/openbiliclaw/web/desktop/index.html"),
    "utf8",
  );
  const desktopJs = readFileSync(
    resolve("../src/openbiliclaw/web/desktop/assets/js/app.js"),
    "utf8",
  );

  assert.match(desktopHtml, /id="contentLibraryBtn"/);
  assert.match(desktopHtml, /id="contentLibraryFavoritesTab"/);
  assert.match(desktopHtml, /id="favoritesCountBadge"/);
  assert.match(desktopHtml, /id="favoritesPage"/);
  assert.match(desktopHtml, /data-delight="favorite"/);
  assert.match(desktopJs, /data-action="favorite"/);
  assert.match(desktopJs, /favoritesPage/);
  assert.match(desktopJs, /refreshFavorites/);
  assert.match(desktopJs, /favoriteStatus/);
  assert.match(desktopJs, /syncFavoriteButtons/);
});

test("extension popup has favorites inside the content library", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupHtml, /id="tabLibrary"[^>]*aria-controls="viewLibrary"/);
  assert.match(popupHtml, /id="tabFavorites"[^>]*role="tab"/);
  assert.match(popupHtml, /id="viewFavorites"/);
  assert.match(popupHtml, /id="favoritesList"/);
  assert.match(popupJs, /delightFavoriteButton/);
  assert.match(popupJs, /toggleSavedWithFeedback\("收藏", delight/);
  assert.match(popupJs, /bindFavoriteToggle\(btn, delight\)/);
  assert.match(popupJs, /function loadFavorites/);
});

test("extension popup recommendation cards have a favorite star toggle", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /toggleSavedWithFeedback\("收藏", item/);
  assert.match(popupJs, /bindFavoriteToggle\(btn, item\)/);
  assert.match(popupJs, /classList\.add\("saved-toggle",\s*"favorite-btn"\)/);
});
