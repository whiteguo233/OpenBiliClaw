import assert from "node:assert/strict";
import test from "node:test";
import { createInstagramScriptSync, type InstagramScriptApi } from "../src/background/instagram-content-scripts.ts";

function fixture() {
  let granted = false;
  let fail = false;
  const scripts = new Map<string, chrome.scripting.RegisteredContentScript>();
  const events: string[] = [];
  const api = {
    permissions: { contains: async () => granted },
    scripting: {
      getRegisteredContentScripts: async () => [...scripts.values()],
      registerContentScripts: async (values: chrome.scripting.RegisteredContentScript[]) => {
        if (fail) { fail = false; throw new Error("transient registration error"); }
        for (const script of values) {
          assert.equal(scripts.has(script.id), false);
          scripts.set(script.id, script);
          events.push(script.id);
        }
      },
      updateContentScripts: async (values: chrome.scripting.RegisteredContentScript[]) => {
        for (const script of values) scripts.set(script.id, script);
      },
      unregisterContentScripts: async ({ ids }: { ids: string[] }) => {
        for (const id of ids) scripts.delete(id);
      },
    },
  } as unknown as InstagramScriptApi;
  return { api, scripts, events, grant: (value: boolean) => { granted = value; },
    failNext: () => { fail = true; } };
}

test("no Instagram scripts without grant; grant registers document_start worlds in order", async () => {
  const f = fixture();
  const sync = createInstagramScriptSync(f.api);
  await sync();
  assert.equal(f.scripts.size, 0);
  f.grant(true);
  await sync();
  assert.deepEqual(f.events, ["openbiliclaw-instagram-isolated", "openbiliclaw-instagram-main"]);
  const [isolated, main] = [...f.scripts.values()];
  assert.deepEqual(isolated.js, ["dist/content/instagram.js"]);
  assert.deepEqual(main.js, ["dist/main/instagram-response-tap.js"]);
  assert.equal(main.world, "MAIN");
  for (const script of f.scripts.values()) {
    assert.equal(script.runAt, "document_start");
    assert.equal(script.persistAcrossSessions, true);
    assert.deepEqual(script.matches, ["https://*.instagram.com/*"]);
  }
});

test("startup and concurrent grants do not duplicate; revoke removes persisted registrations", async () => {
  const f = fixture(); f.grant(true);
  const sync = createInstagramScriptSync(f.api);
  await Promise.all([sync(), sync(), sync()]);
  assert.equal(f.events.length, 2);
  await createInstagramScriptSync(f.api)();
  assert.equal(f.events.length, 2);
  f.grant(false);
  await sync();
  assert.equal(f.scripts.size, 0);
});

test("a registration failure does not poison later permission reconciliation", async () => {
  const f = fixture(); f.grant(true); f.failNext();
  const sync = createInstagramScriptSync(f.api);
  await assert.rejects(sync(), /transient/);
  await sync();
  assert.equal(f.scripts.size, 2);
});

test("worker startup binds grant and revoke listeners and reconciles persisted scripts", async () => {
  const { startInstagramContentScripts, syncInstagramContentScripts } =
    await import("../src/background/instagram-content-scripts.ts");
  const f = fixture();
  const listeners: Record<string, () => void> = {};
  Object.assign(f.api.permissions, {
    onAdded: { addListener: (callback: () => void) => { listeners.add = callback; } },
    onRemoved: { addListener: (callback: () => void) => { listeners.remove = callback; } },
  });
  const previous = Object.getOwnPropertyDescriptor(globalThis, "chrome");
  Object.defineProperty(globalThis, "chrome", { configurable: true, value: f.api });
  try {
    startInstagramContentScripts();
    assert.equal(typeof listeners.add, "function");
    assert.equal(typeof listeners.remove, "function");
    await syncInstagramContentScripts();
    assert.equal(f.scripts.size, 0);
    f.grant(true); listeners.add();
    await syncInstagramContentScripts();
    assert.equal(f.scripts.size, 2);
    f.grant(false); listeners.remove();
    await syncInstagramContentScripts();
    assert.equal(f.scripts.size, 0);
  } finally {
    if (previous) Object.defineProperty(globalThis, "chrome", previous);
    else Reflect.deleteProperty(globalThis, "chrome");
  }
});
