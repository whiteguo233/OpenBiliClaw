import test from "node:test";
import assert from "node:assert/strict";

import {
  buildExtensionUiUrl,
  buildChromeNotificationOptions,
  buildCognitionNotificationId,
  buildDelightNotificationId,
  buildNotificationId,
  buildProfileNotificationUrl,
  openExtensionUi,
  parseNotificationBvid,
  parseCognitionUpdateId,
  parseDelightBvid,
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

test("buildDelightNotificationId and parseDelightBvid round trip bvid", () => {
  const notificationId = buildDelightNotificationId("BV1SURPRISE");

  assert.equal(notificationId, "openbiliclaw-delight:BV1SURPRISE");
  assert.equal(parseDelightBvid(notificationId), "BV1SURPRISE");
  assert.equal(parseDelightBvid("other"), "");
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

test("buildExtensionUiUrl opens recommend tab by default", () => {
  assert.equal(
    buildExtensionUiUrl(),
    "chrome-extension://__EXTENSION_ID__/popup/popup.html?tab=recommend",
  );
});

test("buildExtensionUiUrl carries delight deep-link params", () => {
  assert.equal(
    buildExtensionUiUrl("recommend", { delightBvid: "BV1SURPRISE" }),
    "chrome-extension://__EXTENSION_ID__/popup/popup.html?tab=recommend&delight=BV1SURPRISE",
  );
});

test("openExtensionUi prefers chrome.sidePanel when available", async () => {
  const calls: Array<{ type: string; value: unknown }> = [];
  const chromeLike = {
    runtime: {
      getURL(path: string) {
        return `chrome-extension://__EXTENSION_ID__/${path}`;
      },
    },
    sidePanel: {
      async open(options: { windowId: number }) {
        calls.push({ type: "sidePanel", value: options });
      },
    },
    tabs: {
      async create(options: { url: string }) {
        calls.push({ type: "tab", value: options });
      },
    },
  };

  const result = await openExtensionUi(chromeLike, {
    windowId: 42,
    tab: "profile",
  });

  assert.equal(result, "sidePanel");
  assert.deepEqual(calls, [{ type: "sidePanel", value: { windowId: 42 } }]);
});

test("openExtensionUi uses Firefox sidebarAction when Chrome sidePanel is unavailable", async () => {
  const previousBrowser = (globalThis as Record<string, unknown>).browser;
  const calls: string[] = [];
  (globalThis as Record<string, unknown>).browser = {
    sidebarAction: {
      async open() {
        calls.push("sidebarAction.open");
      },
    },
  };

  try {
    const chromeLike = {
      runtime: {
        getURL(path: string) {
          return `chrome-extension://__EXTENSION_ID__/${path}`;
        },
      },
      tabs: {
        async create(_options: { url: string }) {
          calls.push("tabs.create");
        },
      },
    };

    const result = await openExtensionUi(chromeLike, {
      windowId: 42,
      tab: "profile",
    });

    assert.equal(result, "sidebarPanel");
    assert.deepEqual(calls, ["sidebarAction.open"]);
  } finally {
    if (previousBrowser === undefined) {
      delete (globalThis as Record<string, unknown>).browser;
    } else {
      (globalThis as Record<string, unknown>).browser = previousBrowser;
    }
  }
});

test("openExtensionUi falls back to extension tab when sidePanel is unavailable", async () => {
  const calls: Array<{ type: string; value: unknown }> = [];
  const chromeLike = {
    runtime: {
      getURL(path: string) {
        return `chrome-extension://__EXTENSION_ID__/${path}`;
      },
    },
    tabs: {
      async create(options: { url: string }) {
        calls.push({ type: "tab", value: options });
      },
    },
  };

  const result = await openExtensionUi(chromeLike, {
    windowId: 42,
    tab: "chat",
  });

  assert.equal(result, "tab");
  assert.deepEqual(calls, [
    {
      type: "tab",
      value: { url: "chrome-extension://__EXTENSION_ID__/popup/popup.html?tab=chat" },
    },
  ]);
});

test("openExtensionUi forwards delight deep-link to the popup url", async () => {
  const calls: Array<{ type: string; value: unknown }> = [];
  const chromeLike = {
    runtime: {
      getURL(path: string) {
        return `chrome-extension://__EXTENSION_ID__/${path}`;
      },
    },
    tabs: {
      async create(options: { url: string }) {
        calls.push({ type: "tab", value: options });
      },
    },
  };

  const result = await openExtensionUi(chromeLike, {
    tab: "recommend",
    delightBvid: "BV1SURPRISE",
  });

  assert.equal(result, "tab");
  assert.deepEqual(calls, [
    {
      type: "tab",
      value: {
        url: "chrome-extension://__EXTENSION_ID__/popup/popup.html?tab=recommend&delight=BV1SURPRISE",
      },
    },
  ]);
});
