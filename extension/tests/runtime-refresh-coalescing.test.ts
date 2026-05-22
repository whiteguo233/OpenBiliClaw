import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

function readProjectFile(path: string): string {
  return readFileSync(resolve("..", path), "utf8");
}

test("runtime stream refresh handlers coalesce expensive frontend reloads", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const desktopJs = readProjectFile("src/openbiliclaw/web/desktop/assets/js/app.js");
  const mobileRecommendJs = readProjectFile("src/openbiliclaw/web/js/views/recommend.js");
  const mobileProfileJs = readProjectFile("src/openbiliclaw/web/js/views/profile.js");

  assert.match(popupJs, /function scheduleRecommendationsRefresh/);
  assert.match(popupJs, /function scheduleActivityFeedRefresh/);
  assert.match(popupJs, /recommendationsRefreshInFlight/);
  assert.match(popupJs, /activityFeedRefreshInFlight/);
  assert.doesNotMatch(
    popupJs,
    /if \(event\.type === "activity\.added"\) \{\s*void loadActivityFeed\(\);\s*\}/,
  );
  assert.doesNotMatch(
    popupJs,
    /if \(event\.type === "refresh\.pool_updated"\) \{\s*void initializeRecommendations\(\);\s*\}/,
  );

  assert.match(desktopJs, /function scheduleBackendHydration/);
  assert.match(desktopJs, /function scheduleActivityPageRefresh/);
  assert.match(desktopJs, /backendHydrationInFlight/);
  assert.match(desktopJs, /activityPageRefreshInFlight/);
  assert.doesNotMatch(
    desktopJs,
    /includes\(event\.type\)\) void hydrateFromBackend\(\);/,
  );
  assert.doesNotMatch(
    desktopJs,
    /if \(event\.type === "activity\.added"\) void loadActivityPage\(\{ reset: true \}\);/,
  );

  const poolUpdatedBlock =
    mobileRecommendJs.match(/if \(type === "refresh\.pool_updated"\) \{[\s\S]*?\} else if/)?.[0] ?? "";
  assert.notEqual(poolUpdatedBlock, "", "mobile recommend stream handler should handle pool updates");
  assert.match(mobileRecommendJs, /function scheduleRecommendationItemsRefresh/);
  assert.match(mobileRecommendJs, /recommendationItemsRefreshInFlight/);
  assert.match(poolUpdatedBlock, /scheduleRecommendationItemsRefresh\(\);/);
  assert.doesNotMatch(poolUpdatedBlock, /loadData\(/);

  assert.match(mobileProfileJs, /function scheduleProfileRefresh/);
  assert.match(mobileProfileJs, /profileRefreshInFlight/);
  assert.doesNotMatch(
    mobileProfileJs,
    /if \(type === "profile_updated"\) \{\s*loadData\(\);\s*\}/,
  );
});
