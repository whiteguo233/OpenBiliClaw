import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createServer } from "node:http";

import {
  __resetBackendEndpointForTests,
  updateBackendEndpoint,
} from "../popup/popup-backend-config.js";
import {
  addToFavorite,
  addToWatchLater,
  favoriteStatus,
  fetchFavorites,
  fetchWatchLater,
  removeFromFavorite,
  removeFromWatchLater,
  watchLaterStatus,
} from "../popup/popup-api.js";

function jsonResponse(res, status, payload) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(payload));
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(chunk);
  }
  if (chunks.length === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function makeItem(bvid, surface) {
  return {
    bvid,
    title: `${surface} ${bvid}`,
    up_name: "测试 UP",
    cover_url: "",
    content_url: `https://www.bilibili.com/video/${bvid}`,
    source_platform: "bilibili",
    added_at: "2026-05-31T12:00:00",
  };
}

async function startSavedBackend() {
  const store = {
    "watch-later": new Set(),
    favorites: new Set(),
  };
  const server = createServer(async (req, res) => {
    const url = new URL(req.url || "/", "http://127.0.0.1");
    const match = url.pathname.match(/^\/api\/(watch-later|favorites)(?:\/([^/]+))?$/);
    if (!match) {
      jsonResponse(res, 404, { error: "not_found" });
      return;
    }

    const [, surface, rawBvid] = match;
    const set = store[surface];
    const label = surface === "watch-later" ? "watch-later" : "favorite";

    if (req.method === "GET" && rawBvid) {
      const bvid = decodeURIComponent(rawBvid);
      jsonResponse(res, 200, { saved: set.has(bvid), total: set.size });
      return;
    }
    if (req.method === "GET") {
      jsonResponse(res, 200, {
        items: Array.from(set).map((bvid) => makeItem(bvid, label)),
        total: set.size,
      });
      return;
    }
    if (req.method === "POST") {
      const body = await readJson(req);
      const bvid = String(body.bvid || "").trim();
      if (bvid) set.add(bvid);
      jsonResponse(res, 200, { saved: true, total: set.size });
      return;
    }
    if (req.method === "DELETE" && rawBvid) {
      set.delete(decodeURIComponent(rawBvid));
      jsonResponse(res, 200, { saved: false, total: set.size });
      return;
    }

    jsonResponse(res, 405, { error: "method_not_allowed" });
  });

  await new Promise((resolveListen) => {
    server.listen(0, "127.0.0.1", resolveListen);
  });
  return { server, port: server.address().port };
}

test("popup saved surfaces round-trip through api clients and are wired in the UI", async () => {
  const { server, port } = await startSavedBackend();
  __resetBackendEndpointForTests();
  await updateBackendEndpoint("127.0.0.1", port);

  try {
    assert.deepEqual(await watchLaterStatus("BV1E2E"), { saved: false, total: 0 });
    assert.deepEqual(await favoriteStatus("BV1E2E"), { saved: false, total: 0 });

    assert.deepEqual(await addToWatchLater(" BV1E2E "), { saved: true, total: 1 });
    assert.deepEqual(await addToFavorite("BV1E2E"), { saved: true, total: 1 });
    assert.deepEqual(await watchLaterStatus("BV1E2E"), { saved: true, total: 1 });
    assert.deepEqual(await favoriteStatus("BV1E2E"), { saved: true, total: 1 });
    assert.equal((await fetchWatchLater()).items[0].bvid, "BV1E2E");
    assert.equal((await fetchFavorites()).items[0].bvid, "BV1E2E");

    await removeFromWatchLater("BV1E2E");
    assert.deepEqual(await watchLaterStatus("BV1E2E"), { saved: false, total: 0 });
    assert.deepEqual(await favoriteStatus("BV1E2E"), { saved: true, total: 1 });

    await removeFromFavorite("BV1E2E");
    assert.deepEqual(await favoriteStatus("BV1E2E"), { saved: false, total: 0 });

    const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
    const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
    assert.match(popupHtml, /id="tabWatchLater"/);
    assert.match(popupHtml, /id="viewWatchLater"/);
    assert.match(popupHtml, /id="watchLaterList"/);
    assert.match(popupHtml, /id="tabFavorites"/);
    assert.match(popupHtml, /id="viewFavorites"/);
    assert.match(popupHtml, /id="favoritesList"/);
    assert.match(popupJs, /function loadWatchLater/);
    assert.match(popupJs, /function loadFavorites/);
    assert.match(popupJs, /toggleWatchLaterSaved\(item\.bvid\)/);
    assert.match(popupJs, /toggleFavoriteSaved\(item\.bvid\)/);
    // Saved-card removal must stay optimistic (remove first, restore + 重试 on
    // failure) — the old await-then-remove flow read as "clicking does nothing"
    // whenever the DELETE was slow or failed.
    assert.match(popupJs, /function bindSavedCardRemove/);
    assert.match(popupJs, /remove\.textContent = "重试"/);
  } finally {
    __resetBackendEndpointForTests();
    await new Promise((resolveClose) => server.close(resolveClose));
  }
});
