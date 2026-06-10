import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("recommendation auto-load listens to the shared content scroller", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /content:\s*document\.querySelector\("\.content"\)/);
  assert.match(popupJs, /elements\.content\.scrollHeight - elements\.content\.scrollTop - elements\.content\.clientHeight/);
  assert.match(popupJs, /elements\.content\.addEventListener\("scroll"/);
  assert.match(popupJs, /maybeLoadMoreRecommendations\(\)/);
  assert.doesNotMatch(popupJs, /elements\.viewProfile\.addEventListener\("scroll"/);
});

test("profile cognition history paginates on click only — no scroll auto-load", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  // The "阿B 最近新记住了什么" section must paginate strictly on an explicit
  // "加载更多" click. The old eager scroll auto-load pulled every page the
  // moment the profile neared the bottom, which is exactly what made the
  // section grow unboundedly long — it has to be gone.
  assert.doesNotMatch(popupJs, /maybeLoadMoreCognitionHistory/);

  // The load-more button is the only trigger for fetching older cognition.
  assert.match(
    popupJs,
    /elements\.profileRecentMemoryMore\.addEventListener\("click",[\s\S]*?loadMoreCognitionHistory\(\)/,
  );

  // The shared scroll listener no longer chains cognition pagination.
  const scrollListener =
    popupJs.match(/elements\.content\.addEventListener\("scroll",[\s\S]*?\}\);/)?.[0] ?? "";
  assert.notEqual(scrollListener, "");
  assert.doesNotMatch(scrollListener, /CognitionHistory/);
});

test("recommendation auto-load checks again after render and append", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /function queueRecommendationLoadCheck\(\)/);
  assert.match(popupJs, /recommendationAutoLoadUserArmed/);
  assert.match(popupJs, /initRecommendationAutoLoadIntent\(\)/);
  assert.match(popupJs, /shouldAutoLoadRecommendations/);
  assert.match(popupJs, /queueRecommendationLoadCheck\(\);\r?\n\s*return;\r?\n\s*}/);
  assert.match(popupJs, /finally \{\r?\n\s*state\.loadingMore = false;\r?\n\s*queueRecommendationLoadCheck\(\);/);
});

test("recommendation covers do not rely on native lazy loading inside the popup scroller", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /const image = document\.createElement\("img"\);/);
  assert.doesNotMatch(popupJs, /image\.loading = "lazy"/);
});

test("runtime stream first connect does not re-fetch recommendations after startup load", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /let hasRuntimeStreamConnected = false;/);
  assert.match(
    popupJs,
    /onConnect\(\) \{[\s\S]*?if \(!state\.online\) \{[\s\S]*?setStatus\(true\);[\s\S]*?if \(hasRuntimeStreamConnected\) \{[\s\S]*?scheduleRecommendationsRefresh\(\{ delayMs: 0 \}\);[\s\S]*?\}[\s\S]*?\}[\s\S]*?hasRuntimeStreamConnected = true;/,
  );
});
