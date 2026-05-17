import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import { updateRuntimeToggle } from "../popup/popup-api.js";
import { __resetBackendEndpointForTests } from "../popup/popup-backend-config.js";

test("popup exposes runtime pause toggles in top card and settings drawer", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  for (const id of ["cfgRuntimePauseLlm", "cfgRuntimePauseOnDisconnect"]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist in top card`);
    assert.match(popupJs, new RegExp(`"${id}"`), `${id} should be wired in popup.js`);
  }

  assert.match(popupHtml, /id="cfgPauseOnDisconnect"/);
  assert.match(popupHtml, /后台 LLM 总开关（关闭=省钱模式）/);
  assert.match(popupJs, /pause_on_extension_disconnect:\s*checked\("cfgPauseOnDisconnect"\)/);
  assert.match(popupJs, /cfg\.scheduler\?\.pause_on_extension_disconnect/);
  assert.match(popupJs, /function renderRuntimeToggles/);
  assert.match(popupJs, /function bindRuntimeToggles/);
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
