import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function text(path: string): Promise<string> {
  return readFile(new URL(path, import.meta.url), "utf8");
}

test("optional Instagram does not expand required manifest access", async () => {
  for (const name of ["../manifest.json", "../manifest.firefox.json"]) {
    const manifest = JSON.parse(await text(name));
    assert.equal(manifest.host_permissions.includes("https://*.instagram.com/*"), false);
    assert.ok(manifest.optional_host_permissions.includes("https://*.instagram.com/*"));
    assert.equal(manifest.content_scripts.some((entry: { matches: string[] }) =>
      entry.matches.some((match) => match.includes("instagram.com"))), false);
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
