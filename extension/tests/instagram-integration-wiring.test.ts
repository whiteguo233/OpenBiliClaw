import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function text(path: string): Promise<string> {
  return readFile(new URL(path, import.meta.url), "utf8");
}

test("Chrome and Firefox install isolated listener before MAIN response tap at document_start", async () => {
  for (const name of ["../manifest.json", "../manifest.firefox.json"]) {
    const manifest = JSON.parse(await text(name)) as {
      host_permissions: string[];
      optional_host_permissions: string[];
      content_scripts: Array<{ js: string[]; run_at?: string; world?: string }>;
    };
    assert.equal(manifest.host_permissions.includes("https://*.instagram.com/*"), false);
    assert.ok(manifest.optional_host_permissions.includes("https://*.instagram.com/*"));
    const isolatedIndex = manifest.content_scripts.findIndex((entry) =>
      entry.js.some((file) => file.endsWith("content/instagram.js")));
    const mainIndex = manifest.content_scripts.findIndex((entry) =>
      entry.js.some((file) => file.endsWith("main/instagram-response-tap.js")));
    assert.ok(isolatedIndex >= 0 && mainIndex > isolatedIndex);
    assert.equal(manifest.content_scripts[isolatedIndex].run_at, "document_start");
    assert.equal(manifest.content_scripts[isolatedIndex].world, undefined);
    assert.equal(manifest.content_scripts[mainIndex].run_at, "document_start");
    assert.equal(manifest.content_scripts[mainIndex].world, "MAIN");
  }
});

test("build, service-worker, cookie heartbeat and popup are centrally registered", async () => {
  const [build, worker, cookie, popup, helpers, sourceStatus] = await Promise.all([
    text("../scripts/build.mjs"),
    text("../src/background/service-worker.ts"),
    text("../src/background/cookie-sync.ts"),
    text("../popup/popup.html"),
    text("../popup/popup.js"),
    text("../../src/openbiliclaw/web/shared/source-status.js"),
  ]);
  assert.match(build, /src\/content\/instagram\.ts/);
  assert.match(build, /src\/main\/instagram-response-tap\.ts/);
  for (const symbol of [
    "ensureInstagramTaskRecovery",
    "startInstagramTaskPolling",
    "handleInstagramTaskAlarm",
    "handleInstagramTaskResult",
    "handleInstagramTaskProgress",
    "instagram_task_available",
  ]) assert.match(worker, new RegExp(symbol));
  assert.match(cookie, /INSTAGRAM_LOGIN_COOKIE_NAME = "sessionid"/);
  assert.match(cookie, /\/sources\/instagram\/credential/);
  assert.match(cookie, /JSON\.stringify\(\{ kind: "login_state", value: loggedIn, source \}\)/);
  assert.doesNotMatch(cookie, /sources\/instagram\/credential[\s\S]{0,300}cookie:/i);
  assert.match(popup, /data-source-card="instagram"/);
  assert.match(popup, /data-source-status="instagram"/);
  assert.match(helpers, /cfgInstagramEnabled/);
  assert.match(helpers, /chrome\.permissions\.request/);
  assert.match(sourceStatus, /"instagram"/);
});
