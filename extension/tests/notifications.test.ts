import test from "node:test";
import assert from "node:assert/strict";

import {
  buildChromeNotificationOptions,
  buildCognitionNotificationId,
  buildNotificationId,
  buildProfileNotificationUrl,
  parseNotificationBvid,
  parseCognitionUpdateId,
} from "../src/background/notifications.ts";

test("buildNotificationId and parseNotificationBvid round trip bvid", () => {
  const notificationId = buildNotificationId("BV1ROUND");

  assert.equal(notificationId, "openbiliclaw-recommendation:BV1ROUND");
  assert.equal(parseNotificationBvid(notificationId), "BV1ROUND");
  assert.equal(parseNotificationBvid("other"), "");
});

test("buildCognitionNotificationId and parseCognitionUpdateId round trip update id", () => {
  const notificationId = buildCognitionNotificationId("cog-1");

  assert.equal(notificationId, "openbiliclaw-cognition:cog-1");
  assert.equal(parseCognitionUpdateId(notificationId), "cog-1");
  assert.equal(parseCognitionUpdateId("other"), "");
});

test("buildChromeNotificationOptions fills stable fallback copy", () => {
  const options = buildChromeNotificationOptions({
    recommendation_id: 1,
    bvid: "BV1TEST",
    title: "",
    reason: "",
  });

  assert.equal(options.type, "basic");
  assert.equal(options.title, "阿B 给你补到一条新内容");
  assert.equal(options.message, "这条大概率会对你的胃口。");
  assert.equal(options.iconUrl, "icons/icon128.png");
});

test("buildChromeNotificationOptions builds cognition notification copy", () => {
  const options = buildChromeNotificationOptions({
    id: "cog-1",
    kind: "interest_added",
    summary: "阿B 现在更确定你会吃国际时事深拆这一口。",
  });

  assert.equal(options.type, "basic");
  assert.equal(options.title, "阿B 又对你多看清了一点");
  assert.equal(options.message, "阿B 现在更确定你会吃国际时事深拆这一口。");
});

test("buildProfileNotificationUrl opens profile tab in extension page", () => {
  assert.equal(
    buildProfileNotificationUrl(),
    "chrome-extension://__EXTENSION_ID__/popup/popup.html?tab=profile",
  );
});
