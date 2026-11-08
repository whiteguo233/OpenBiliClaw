import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

function sourceBlock(source: string, start: string, end: string): string {
  const startIndex = source.indexOf(start);
  assert.notEqual(startIndex, -1, `missing start marker: ${start}`);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(endIndex, -1, `missing end marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

test("mobile stream removes only negative delight feedback", () => {
  const recommendJs = readFileSync(
    resolve("../src/openbiliclaw/web/js/views/recommend.js"),
    "utf8",
  );
  const chatJs = readFileSync(
    resolve("../src/openbiliclaw/web/js/views/chat.js"),
    "utf8",
  );

  assert.doesNotMatch(
    recommendJs,
    /type === "delight\.liked"\s*\|\|\s*type === "delight\.disliked"/,
  );
  assert.match(recommendJs, /type === "delight\.disliked"/);
  assert.doesNotMatch(
    chatJs,
    /type === "delight\.liked"\s*\|\|\s*type === "delight\.disliked"/,
  );
  assert.doesNotMatch(
    chatJs,
    /if \(scope === "delight"\) \{\s*delightMsgs = delightMsgs\.filter/,
  );
  assert.match(chatJs, /if \(permanent\) \{\s*markDelightSent[\s\S]*?delightMsgs = delightMsgs\.filter/);
});

test("extension delight banner keeps positive actions visible", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const openBlock = sourceBlock(
    popupJs,
    "const openButton = createActionButton(",
    "const likeButton = createActionButton(",
  );
  const likeBlock = sourceBlock(
    popupJs,
    "const likeButton = createActionButton(",
    "const rejectButton = createActionButton(",
  );
  const rejectBlock = sourceBlock(
    popupJs,
    "const rejectButton = createActionButton(",
    "const chatButton = createActionButton(",
  );

  assert.doesNotMatch(openBlock, /shiftDelightQueue|removeCurrentDelight/);
  assert.doesNotMatch(likeBlock, /shiftDelightQueue|removeCurrentDelight|rememberDismissedDelight/);
  assert.match(rejectBlock, /removeCurrentDelight/);
});

test("extension delight close persists handled content as seen", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const rememberBlock = sourceBlock(
    popupJs,
    "function rememberDismissedDelight(bvid)",
    "// ── Delight queue helpers",
  );
  const dismissBlock = sourceBlock(
    popupJs,
    'dismiss.className = "delight-banner-dismiss";',
    "banner.append(row, dismiss);",
  );

  assert.match(rememberBlock, /respondToDelight\(bvid, "dismiss"\)/);
  assert.doesNotMatch(rememberBlock, /markDelightSent/);
  assert.match(dismissBlock, /aria-label", "看过了，不再推荐"/);
  assert.match(dismissBlock, /await rememberDismissedDelight\(delight\.bvid\)/);
});

test("desktop delight actions remove only explicit negative responses", () => {
  const desktopJs = readFileSync(
    resolve("../src/openbiliclaw/web/desktop/assets/js/app.js"),
    "utf8",
  );
  const responseBlock = sourceBlock(
    desktopJs,
    "const feedbackToast = response === \"like\"",
    "function openMessageChat(msg)",
  );

  assert.match(
    responseBlock,
    /if \(response === "dislike" \|\| response === "dismiss"\) \{\s*state\.delights = state\.delights\.filter/,
  );
});

test("mobile delight response-loss retry reuses id when the title changes", async () => {
  const storage = new Map<string, string>();
  const submitted: Array<Record<string, string>> = [];
  (globalThis as any).location = new URL("http://127.0.0.1:8420/m/");
  (globalThis as any).window = { dispatchEvent() { return true; } };
  (globalThis as any).CustomEvent = class {
    type: string;
    constructor(type: string) { this.type = type; }
  };
  (globalThis as any).localStorage = {
    getItem(key: string) { return storage.get(key) ?? null; },
    setItem(key: string, value: string) { storage.set(key, value); },
  };
  let attempt = 0;
  (globalThis as any).fetch = async (_url: string, options: any = {}) => {
    submitted.push(JSON.parse(String(options.body || "{}")));
    attempt += 1;
    if (attempt === 1) throw new Error("response lost after commit");
    return {
      ok: true,
      status: 200,
      async json() { return { ok: true }; },
    };
  };

  const api = await import(`../../src/openbiliclaw/web/js/api.js?delight-retry=${Date.now()}`);
  await assert.rejects(
    () => api.respondToDelight("BV1STABLE", "like", "旧标题"),
    /response lost/,
  );
  await api.respondToDelight("BV1STABLE", "like", "刷新后的新标题");

  assert.equal(submitted.length, 2);
  assert.equal(submitted[0].request_id, submitted[1].request_id);
  assert.notEqual(submitted[0].title, submitted[1].title);
});

test("desktop delight durable identity excludes mutable presentation copy", () => {
  const desktopJs = readFileSync(
    resolve("../src/openbiliclaw/web/desktop/assets/js/app.js"),
    "utf8",
  );
  const responseBlock = sourceBlock(
    desktopJs,
    'const feedbackToast = response === "like"',
    "function openMessageChat(msg)",
  );

  assert.match(responseBlock, /JSON\.stringify\(\[delight\.bvid, response\]\)/);
  assert.doesNotMatch(
    responseBlock,
    /JSON\.stringify\(\[delight\.bvid, response, delight\.title/,
  );
  const viewBlock = sourceBlock(
    desktopJs,
    'if (response === "view") {',
    'const feedbackToast = response === "like"',
  );
  assert.match(viewBlock, /requestJson\(ENDPOINTS\.delightRespond/);
  assert.doesNotMatch(viewBlock, /requestJsonWithPendingId/);
});

test("mobile recommendation-click retry uses recommendation id before a signed URL", async () => {
  const storage = new Map<string, string>();
  const submitted: Array<Record<string, string>> = [];
  (globalThis as any).location = {
    protocol: "http:",
    host: "127.0.0.1:8420",
    href: "http://127.0.0.1:8420/web/",
  };
  (globalThis as any).localStorage = {
    getItem(key: string) { return storage.get(key) ?? null; },
    setItem(key: string, value: string) { storage.set(key, value); },
  };
  let attempt = 0;
  (globalThis as any).fetch = async (_url: string, options: any = {}) => {
    submitted.push(JSON.parse(String(options.body || "{}")));
    attempt += 1;
    if (attempt === 1) throw new Error("response lost after commit");
    return {
      ok: true,
      status: 200,
      async json() { return { ok: true }; },
    };
  };

  const api = await import(`../../src/openbiliclaw/web/js/api.js?click-retry=${Date.now()}`);
  const stable = {
    recommendation_id: 42,
    source_platform: "xiaohongshu",
  };
  assert.equal(await api.reportClick({
    ...stable,
    content_url: "https://www.xiaohongshu.com/explore/xhs-note-stable?xsec_token=old",
  }), false);
  assert.equal(await api.reportClick({
    ...stable,
    content_url: "https://www.xiaohongshu.com/explore/xhs-note-stable?xsec_token=new",
  }), true);

  assert.equal(submitted.length, 2);
  assert.equal(submitted[0].request_id, submitted[1].request_id);
  assert.notEqual(submitted[0].content_url, submitted[1].content_url);
});

test("all recommendation-click clients use URL only without recommendation or content ids", () => {
  const popupApi = readFileSync(resolve("popup", "popup-api.js"), "utf8");
  const popupBlock = sourceBlock(
    popupApi,
    "export async function reportRecommendationClick(payload)",
    "export async function sendChatMessage",
  );
  assert.match(popupBlock, /if \(!stableRecommendationId && !stableContentId\)/);
  assert.match(popupBlock, /stableContentId \|\| fallbackUrl/);

  const desktopJs = readFileSync(
    resolve("../src/openbiliclaw/web/desktop/assets/js/app.js"),
    "utf8",
  );
  const desktopBlock = sourceBlock(
    desktopJs,
    "function trackRecommendationClick(item)",
    "function openRecommendation(item, card)",
  );
  assert.match(desktopBlock, /if \(stableRecommendationId == null && !stableContentId\)/);
  assert.match(desktopBlock, /stableContentId \|\| fallbackUrl/);
});
