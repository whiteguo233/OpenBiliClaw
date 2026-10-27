import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";

for (const enabled of [true, false]) {
  test(`saving Instagram enabled=${enabled} writes config without requesting or removing site access`, async () => {
    const source = readFileSync(new URL("../popup/popup.js", import.meta.url), "utf8");
    const handler = source.match(/saveBtn\.addEventListener\("click"[\s\S]*?\n  \}\);/)?.[0];
    assert.ok(handler);
    let click: (() => Promise<void>) | undefined;
    const permissionCalls: string[] = [];
    const writes: unknown[] = [];
    const payload = { sources: { instagram: { enabled } } };
    const endpoint = { scheme: "http", host: "127.0.0.1", port: "8420" };
    const context = {
      settingsSaveInFlight: false,
      settingsDirtyFields: new Set(["cfgInstagramEnabled"]),
      saveBtn: {
        textContent: "保存配置",
        addEventListener: (_event: string, callback: () => Promise<void>) => { click = callback; },
      },
      toast: { hidden: false },
      renderSettingsDirty: () => {},
      checked: () => enabled,
      chrome: {
        permissions: {
          request: async () => { permissionCalls.push("request"); return false; },
          remove: async () => { permissionCalls.push("remove"); return false; },
        },
      },
      // Catch regressions to the old authorization gate without waiting 30 seconds.
      requestPermissionWithTimeout: async (request: () => Promise<boolean>) =>
        await request() ? "granted" : "denied",
      backendSchemeInput: null,
      backendHostInput: null,
      backendPortInput: null,
      HTMLSelectElement: class {},
      HTMLInputElement: class {},
      getBackendEndpointConfig: async () => endpoint,
      updateBackendEndpoint: async () => endpoint,
      collectForm: () => payload,
      updateConfig: async (value: unknown) => { writes.push(value); return { reloaded: true }; },
      clearSettingsDirty: () => context.settingsDirtyFields.clear(),
      showToast: () => {},
      renderStructuredConfigError: () => false,
      SOURCE_SAVE_ERROR_MESSAGES: {},
      state: { runtimeConfig: {} },
      setSaveButtonMode: () => { context.saveBtn.textContent = "保存配置"; },
    };
    vm.runInNewContext(handler, context);
    assert.ok(click);
    await click();
    assert.deepEqual(permissionCalls, [], "source settings must not change required host grants");
    assert.deepEqual(writes, [payload]);
    assert.equal(context.settingsSaveInFlight, false);
    assert.equal(context.settingsDirtyFields.size, 0);
    assert.equal(context.saveBtn.textContent, "保存配置");
  });
}
