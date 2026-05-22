import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import { updateRuntimeToggle } from "../popup/popup-api.js";
import { __resetBackendEndpointForTests } from "../popup/popup-backend-config.js";

test("scheduler toggles exist only in settings drawer, not in recommendation card", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  // Runtime toggles removed from recommendation card
  for (const id of ["cfgRuntimePauseLlm", "cfgRuntimePauseOnDisconnect"]) {
    assert.doesNotMatch(popupHtml, new RegExp(`id="${id}"`), `${id} should not exist in HTML`);
    assert.doesNotMatch(popupJs, new RegExp(`"${id}"`), `${id} should not be wired in popup.js`);
  }

  // Settings drawer retains the toggles
  assert.match(popupHtml, /id="cfgSchedulerEnabled"/);
  assert.match(popupHtml, /id="cfgPauseOnDisconnect"/);
  assert.match(popupHtml, /停止后台 LLM 请求/);
  assert.match(popupHtml, /候选池为空时可能暂时没有推荐/);
  assert.match(popupHtml, /离线期间不会自动补新内容/);
  assert.match(popupJs, /pause_on_extension_disconnect:\s*checked\("cfgPauseOnDisconnect"\)/);
  assert.match(popupJs, /cfg\.scheduler\?\.pause_on_extension_disconnect/);
  assert.match(popupJs, /function renderRuntimeToggles/);
});

test("updateRuntimeToggle sends the matching scheduler patch", async () => {
  __resetBackendEndpointForTests();
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { ok: true, config: { scheduler: {} } };
      },
    };
  };

  await updateRuntimeToggle("pause_llm", true);
  await updateRuntimeToggle("pause_on_disconnect", true);

  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/config");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    scheduler: { enabled: false },
  });
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    scheduler: { pause_on_extension_disconnect: true },
  });
});
