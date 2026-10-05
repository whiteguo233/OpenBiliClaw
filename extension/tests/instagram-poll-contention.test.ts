import assert from "node:assert/strict";
import test from "node:test";
import { installChromeMock } from "./helpers/chrome-mock.ts";
import { tryAcquireDispatcherMutex, releaseDispatcherMutex } from "../src/background/dispatcher-mutex.ts";

test("a contended periodic poll gets its own durable retry after the other source releases", async () => {
  const browser = installChromeMock();
  const alarms = new Map<string, chrome.alarms.AlarmCreateInfo>();
  chrome.alarms = {
    create: async (name: string, info: chrome.alarms.AlarmCreateInfo) => { alarms.set(name, info); },
    clear: async (name: string) => alarms.delete(name),
  } as typeof chrome.alarms;
  const flush = async () => { for (let i = 0; i < 80; i++) await Promise.resolve(); };
  try {
    const dispatcher = await import("../src/background/instagram-task-dispatcher.ts?poll-contention");
    assert.equal(tryAcquireDispatcherMutex("other-source"), true);
    dispatcher.handleInstagramTaskAlarm("openbiliclaw-instagram-task-poll");
    await flush();
    assert.equal(browser.fetchCalls.length, 0, "no claim while another source owns the mutex");
    const retry = alarms.get("openbiliclaw-instagram-poll-retry");
    assert.ok(retry, "coincident minute alarms must not starve Instagram forever");
    assert.equal(retry.delayInMinutes, 0.5, "bounded wake supported by MV3 alarms");
    releaseDispatcherMutex("other-source");
    dispatcher.handleInstagramTaskAlarm("openbiliclaw-instagram-poll-retry");
    await flush();
    assert.equal(browser.fetchCalls.filter(call => call.url.endsWith("/sources/instagram/next-task")).length, 1);
    assert.equal(alarms.has("openbiliclaw-instagram-poll-retry"), false);
  } finally { releaseDispatcherMutex("other-source"); browser.restore(); }
});
