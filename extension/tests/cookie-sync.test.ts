import test from "node:test";
import assert from "node:assert/strict";

type Cookie = {
  name: string;
  value: string;
  domain?: string;
};

type CookieChangeListener = (changeInfo: {
  cookie: { name: string; domain: string };
  removed: boolean;
}) => void;

let importCounter = 0;

async function importCookieSync() {
  importCounter += 1;
  return import(`../src/background/cookie-sync.ts?case=${importCounter}`);
}

function installChromeMock(cookies: Cookie[]) {
  const listeners: CookieChangeListener[] = [];
  const alarms: Array<{ name: string; info: Record<string, number> }> = [];

  globalThis.chrome = {
    cookies: {
      getAll: async (details?: { domain?: string }) => {
        const domain = details?.domain?.toLowerCase();
        if (!domain) return cookies;
        return cookies.filter((cookie) => {
          const cookieDomain = cookie.domain?.replace(/^\./, "").toLowerCase();
          return !cookieDomain || cookieDomain === domain || cookieDomain.endsWith(`.${domain}`);
        });
      },
      onChanged: {
        addListener: (listener: CookieChangeListener) => {
          listeners.push(listener);
        },
      },
    },
    alarms: {
      create: (name: string, info: Record<string, number>) => {
        alarms.push({ name, info });
      },
    },
  } as unknown as typeof chrome;

  return { listeners, alarms };
}

test("startCookieSync retries quickly when the backend is not ready", async () => {
  const { startCookieSync } = await importCookieSync();
  const { alarms } = installChromeMock([
    { name: "SESSDATA", value: "sess" },
    { name: "bili_jct", value: "csrf" },
    { name: "DedeUserID", value: "42" },
  ]);
  globalThis.fetch = async () => {
    throw new Error("backend down");
  };

  startCookieSync();
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.deepEqual(alarms.at(-1), {
    name: "openbiliclaw-cookie-sync-bili",
    info: { delayInMinutes: 1, periodInMinutes: 1 },
  });
});

test("startCookieSync registers cookie listener only once", async () => {
  const { startCookieSync } = await importCookieSync();
  const { listeners } = installChromeMock([
    { name: "SESSDATA", value: "sess" },
    { name: "bili_jct", value: "csrf" },
    { name: "DedeUserID", value: "42" },
  ]);
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ ok: true, authenticated: true }), { status: 200 });

  startCookieSync();
  startCookieSync();

  assert.equal(listeners.length, 1);
});

test("cookie sync runtime event posts the current bilibili cookie immediately", async () => {
  const { handleCookieSyncRuntimeEvent } = await importCookieSync();
  installChromeMock([
    { name: "SESSDATA", value: "sess" },
    { name: "bili_jct", value: "csrf" },
    { name: "DedeUserID", value: "42" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, authenticated: true }), { status: 200 });
  };

  const handled = handleCookieSyncRuntimeEvent({
    type: "bilibili_cookie_sync_requested",
    reason: "missing_cookie",
  });
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(handled, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/bilibili/cookie");
  assert.deepEqual(calls[0].body, {
    cookie: "SESSDATA=sess; bili_jct=csrf; DedeUserID=42",
    source: "runtime-stream-request",
    validate_with_bilibili: true,
  });
});

test("readDouyinCookieHeader returns the current douyin cookie header", async () => {
  const { readDouyinCookieHeader } = await importCookieSync();
  installChromeMock([
    { name: "msToken", value: "token" },
    { name: "ttwid", value: "tw" },
    { name: "sessionid", value: "sess" },
  ]);

  assert.equal(await readDouyinCookieHeader(), "msToken=token; ttwid=tw; sessionid=sess");
});

test("readDouyinCookieHeader accepts logged-in douyin cookies without msToken", async () => {
  const { readDouyinCookieHeader } = await importCookieSync();
  installChromeMock([
    { name: "sessionid", value: "sess" },
    { name: "sid_guard", value: "guard" },
    { name: "ttwid", value: "tw" },
  ]);

  assert.equal(await readDouyinCookieHeader(), "sessionid=sess; sid_guard=guard; ttwid=tw");
});

test("cookie sync runtime event posts the current douyin cookie immediately", async () => {
  const { handleCookieSyncRuntimeEvent } = await importCookieSync();
  installChromeMock([
    { name: "msToken", value: "token" },
    { name: "ttwid", value: "tw" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, has_cookie: true }), { status: 200 });
  };

  const handled = handleCookieSyncRuntimeEvent({
    type: "douyin_cookie_sync_requested",
    reason: "missing_cookie",
  });
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(handled, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/sources/dy/cookie");
  assert.deepEqual(calls[0].body, {
    cookie: "msToken=token; ttwid=tw",
    source: "runtime-stream-request",
  });
});

test("legacy shared cookie sync alarm refreshes bilibili and douyin cookies", async () => {
  // Pre-split alarms persist across extension updates — the legacy name
  // must still trigger a full round instead of being silently dropped.
  const { handleCookieSyncAlarm } = await importCookieSync();
  installChromeMock([
    { name: "SESSDATA", value: "sess", domain: ".bilibili.com" },
    { name: "bili_jct", value: "csrf", domain: ".bilibili.com" },
    { name: "DedeUserID", value: "42", domain: ".bilibili.com" },
    { name: "sessionid", value: "dy-sess", domain: ".douyin.com" },
    { name: "sid_guard", value: "dy-guard", domain: ".douyin.com" },
    { name: "ttwid", value: "dy-tw", domain: ".douyin.com" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    if (String(url).endsWith("/api/sources/dy/cookie")) {
      return new Response(JSON.stringify({ ok: true, has_cookie: true }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true, authenticated: true }), { status: 200 });
  };

  const handled = handleCookieSyncAlarm("openbiliclaw-cookie-sync");
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(handled, true);
  // Regression: the bilibili + douyin alarm paths still fire after adding X.
  // (No x.com cookies installed here, so X sends nothing.)
  assert.deepEqual(
    calls.map((call) => call.url).sort(),
    [
      "http://127.0.0.1:8420/api/bilibili/cookie",
      "http://127.0.0.1:8420/api/sources/dy/cookie",
    ],
  );
  assert.deepEqual(calls.find((call) => call.url.endsWith("/api/sources/dy/cookie"))?.body, {
    cookie: "sessionid=dy-sess; sid_guard=dy-guard; ttwid=dy-tw",
    source: "hourly-alarm",
  });
});

test("readXCookieHeader returns the header only when auth_token and ct0 are present", async () => {
  const { readXCookieHeader } = await importCookieSync();
  installChromeMock([
    { name: "auth_token", value: "at", domain: ".x.com" },
    { name: "ct0", value: "csrf", domain: ".x.com" },
    { name: "guest_id", value: "gx", domain: ".x.com" },
  ]);

  assert.equal(await readXCookieHeader(), "auth_token=at; ct0=csrf; guest_id=gx");
});

test("readXCookieHeader returns null without ct0", async () => {
  const { readXCookieHeader } = await importCookieSync();
  installChromeMock([
    { name: "auth_token", value: "at", domain: ".x.com" },
    { name: "guest_id", value: "gx", domain: ".x.com" },
  ]);

  assert.equal(await readXCookieHeader(), null);
});

test("readXCookieHeader returns null without auth_token", async () => {
  const { readXCookieHeader } = await importCookieSync();
  installChromeMock([
    { name: "ct0", value: "csrf", domain: ".x.com" },
    { name: "guest_id", value: "gx", domain: ".x.com" },
  ]);

  assert.equal(await readXCookieHeader(), null);
});

test("cookie sync runtime event posts the current x cookie immediately", async () => {
  const { handleCookieSyncRuntimeEvent } = await importCookieSync();
  installChromeMock([
    { name: "auth_token", value: "at", domain: ".x.com" },
    { name: "ct0", value: "csrf", domain: ".x.com" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, has_cookie: true }), { status: 200 });
  };

  const handled = handleCookieSyncRuntimeEvent({
    type: "x_cookie_sync_requested",
    reason: "missing_cookie",
  });
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(handled, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/sources/x/cookie");
  assert.deepEqual(calls[0].body, {
    cookie: "auth_token=at; ct0=csrf",
    source: "runtime-stream-request",
  });
});

test("readRedditCookieHeader returns the header only when reddit_session is present", async () => {
  const { readRedditCookieHeader } = await importCookieSync();
  installChromeMock([
    { name: "reddit_session", value: "rs", domain: ".reddit.com" },
    { name: "loid", value: "loid", domain: ".reddit.com" },
  ]);

  assert.equal(await readRedditCookieHeader(), "reddit_session=rs; loid=loid");
});

test("readRedditCookieHeader returns null without reddit_session", async () => {
  const { readRedditCookieHeader } = await importCookieSync();
  installChromeMock([{ name: "loid", value: "loid", domain: ".reddit.com" }]);

  assert.equal(await readRedditCookieHeader(), null);
});

test("cookie sync runtime event posts the current reddit cookie immediately", async () => {
  const { handleCookieSyncRuntimeEvent } = await importCookieSync();
  installChromeMock([
    { name: "reddit_session", value: "rs", domain: ".reddit.com" },
    { name: "loid", value: "loid", domain: ".reddit.com" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, has_cookie: true }), { status: 200 });
  };

  const handled = handleCookieSyncRuntimeEvent({
    type: "reddit_cookie_sync_requested",
    reason: "missing_cookie",
  });
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(handled, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/sources/reddit/cookie");
  assert.deepEqual(calls[0].body, {
    cookie: "reddit_session=rs; loid=loid",
    source: "runtime-stream-request",
  });
});

test("legacy shared cookie sync alarm refreshes bilibili, douyin AND x cookies together", async () => {
  const { handleCookieSyncAlarm } = await importCookieSync();
  installChromeMock([
    { name: "SESSDATA", value: "sess", domain: ".bilibili.com" },
    { name: "bili_jct", value: "csrf", domain: ".bilibili.com" },
    { name: "DedeUserID", value: "42", domain: ".bilibili.com" },
    { name: "sessionid", value: "dy-sess", domain: ".douyin.com" },
    { name: "sid_guard", value: "dy-guard", domain: ".douyin.com" },
    { name: "ttwid", value: "dy-tw", domain: ".douyin.com" },
    { name: "auth_token", value: "x-at", domain: ".x.com" },
    { name: "ct0", value: "x-csrf", domain: ".x.com" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    if (String(url).endsWith("/api/bilibili/cookie")) {
      return new Response(JSON.stringify({ ok: true, authenticated: true }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true, has_cookie: true }), { status: 200 });
  };

  const handled = handleCookieSyncAlarm("openbiliclaw-cookie-sync");
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(handled, true);
  assert.deepEqual(
    calls.map((call) => call.url).sort(),
    [
      "http://127.0.0.1:8420/api/bilibili/cookie",
      "http://127.0.0.1:8420/api/sources/dy/cookie",
      "http://127.0.0.1:8420/api/sources/x/cookie",
    ],
  );
  assert.deepEqual(calls.find((call) => call.url.endsWith("/api/sources/x/cookie"))?.body, {
    cookie: "auth_token=x-at; ct0=x-csrf",
    source: "hourly-alarm",
  });
});

test("startCookieSync triggers a reddit cookie sync at startup", async () => {
  const { startCookieSync } = await importCookieSync();
  installChromeMock([
    { name: "reddit_session", value: "rs", domain: ".reddit.com" },
    { name: "loid", value: "loid", domain: ".reddit.com" },
  ]);
  const calls: string[] = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return new Response(JSON.stringify({ ok: true, has_cookie: true }), { status: 200 });
  };

  startCookieSync();
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.ok(calls.includes("http://127.0.0.1:8420/api/sources/reddit/cookie"));
});

test("onChanged on a reddit.com session cookie schedules a sync", async () => {
  const { startCookieSync } = await importCookieSync();
  const { listeners } = installChromeMock([
    { name: "reddit_session", value: "rs", domain: ".reddit.com" },
  ]);
  const calls: string[] = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return new Response(JSON.stringify({ ok: true, has_cookie: true }), { status: 200 });
  };

  startCookieSync();
  await new Promise((resolve) => setTimeout(resolve, 0));
  calls.length = 0;

  listeners[0]({ cookie: { name: "reddit_session", domain: ".reddit.com" }, removed: false });
  await new Promise((resolve) => setTimeout(resolve, 2_100));

  assert.ok(calls.includes("http://127.0.0.1:8420/api/sources/reddit/cookie"));
});

test("per-platform alarm only syncs its own platform", async () => {
  const { handleCookieSyncAlarm } = await importCookieSync();
  installChromeMock([
    { name: "SESSDATA", value: "sess", domain: ".bilibili.com" },
    { name: "bili_jct", value: "csrf", domain: ".bilibili.com" },
    { name: "DedeUserID", value: "42", domain: ".bilibili.com" },
    { name: "sessionid", value: "dy-sess", domain: ".douyin.com" },
    { name: "auth_token", value: "x-at", domain: ".x.com" },
    { name: "ct0", value: "x-csrf", domain: ".x.com" },
  ]);
  const calls: string[] = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return new Response(JSON.stringify({ ok: true, authenticated: true, has_cookie: true }), {
      status: 200,
    });
  };

  const handled = handleCookieSyncAlarm("openbiliclaw-cookie-sync-dy");
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(handled, true);
  assert.deepEqual(calls, ["http://127.0.0.1:8420/api/sources/dy/cookie"]);
});

test("a douyin sync failure does not reschedule the bilibili alarm", async () => {
  const { handleCookieSyncAlarm } = await importCookieSync();
  const { alarms } = installChromeMock([
    { name: "sessionid", value: "dy-sess", domain: ".douyin.com" },
  ]);
  globalThis.fetch = async () => {
    throw new Error("backend down");
  };

  handleCookieSyncAlarm("openbiliclaw-cookie-sync-dy");
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.deepEqual(alarms.map((alarm) => alarm.name), ["openbiliclaw-cookie-sync-dy"]);
  assert.deepEqual(alarms.at(-1)?.info, { delayInMinutes: 1, periodInMinutes: 1 });
});

test("startCookieSync triggers an x cookie sync at startup", async () => {
  const { startCookieSync } = await importCookieSync();
  installChromeMock([
    { name: "auth_token", value: "at", domain: ".x.com" },
    { name: "ct0", value: "csrf", domain: ".x.com" },
  ]);
  const calls: string[] = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return new Response(JSON.stringify({ ok: true, has_cookie: true }), { status: 200 });
  };

  startCookieSync();
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.ok(calls.includes("http://127.0.0.1:8420/api/sources/x/cookie"));
});

test("onChanged on an x.com session cookie schedules a sync", async () => {
  const { startCookieSync } = await importCookieSync();
  const { listeners } = installChromeMock([
    { name: "auth_token", value: "at", domain: ".x.com" },
    { name: "ct0", value: "csrf", domain: ".x.com" },
  ]);
  const calls: string[] = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return new Response(JSON.stringify({ ok: true, has_cookie: true }), { status: 200 });
  };

  startCookieSync();
  await new Promise((resolve) => setTimeout(resolve, 0));
  calls.length = 0;

  listeners[0]({ cookie: { name: "ct0", domain: ".x.com" }, removed: false });
  await new Promise((resolve) => setTimeout(resolve, 2_100));

  assert.ok(calls.includes("http://127.0.0.1:8420/api/sources/x/cookie"));
});
