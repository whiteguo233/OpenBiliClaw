import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import * as helpers from "../popup/popup-helpers.js";

test("actual save handler leaves permission-wait after timeout without saving or dropping edits", async () => {
  const source = readFileSync(new URL("../popup/popup.js", import.meta.url), "utf8");
  const handler = source.match(/saveBtn\.addEventListener\("click"[\s\S]*?\n  \}\);/)?.[0];
  assert.ok(handler);
  let click: (() => Promise<void>) | undefined;
  let grant: (value: boolean) => void = () => {};
  const permission = new Promise<boolean>((resolve) => { grant = resolve; });
  const messages: string[] = [];
  let writes = 0;
  const context = {
    settingsSaveInFlight: false,
    settingsDirtyFields: new Set(["cfgInstagramEnabled"]),
    saveBtn: {
      textContent: "保存配置",
      addEventListener: (_event: string, callback: () => Promise<void>) => { click = callback; },
    },
    toast: { hidden: false },
    renderSettingsDirty: () => {},
    checked: () => true,
    chrome: { permissions: { request: () => permission } },
    requestPermissionWithTimeout: (request: () => Promise<boolean>) =>
      helpers.requestPermissionWithTimeout(request, { timeoutMs: 5 }),
    showToast: (message: string) => messages.push(message),
    renderStructuredConfigError: () => false,
    BANGUMI_SAVE_ERROR_MESSAGES: {},
    state: { runtimeConfig: {} },
    setSaveButtonMode: () => { context.saveBtn.textContent = "保存配置"; },
    updateConfig: () => { writes += 1; },
  };
  vm.runInNewContext(handler, context);
  assert.ok(click);
  void click();
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.equal(context.settingsSaveInFlight, false);
  assert.equal(context.saveBtn.textContent, "保存配置");
  assert.equal(context.settingsDirtyFields.size, 1);
  assert.ok(messages.some((message) => message.includes("授权") && message.includes("超时")));
  grant(true);
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(writes, 0, "a late grant must not save the abandoned form");
});
