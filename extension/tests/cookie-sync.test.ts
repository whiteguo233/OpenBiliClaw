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

  assert.deepEqual(
    alarms.find(
      (alarm) =>
        alarm.name === "openbiliclaw-cookie-sync-bili" && alarm.info.delayInMinutes === 1,
    ),
    {
      name: "openbiliclaw-cookie-sync-bili",
      info: { delayInMinutes: 1, periodInMinutes: 1 },
    },
  );
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

test("V2EX login-state checks only the cookie name and never reads its secret value", async () => {
  const { readV2EXLoginState } = await importCookieSync();
  const sessionCookie = {
    name: "A2",
    domain: ".v2ex.com",
  } as Cookie;
  Object.defineProperty(sessionCookie, "value", {
    get() {
      throw new Error("V2EX cookie value must not be read");
    },
  });
  installChromeMock([sessionCookie]);

  assert.equal(await readV2EXLoginState(), true);
});

test("Instagram login-state heartbeat sends a boolean and never reads or uploads sessionid", async () => {
  const { readInstagramLoginState, syncInstagramLoginStateToBackend } = await importCookieSync();
  const sessionCookie = {
    name: "sessionid",
    domain: ".instagram.com",
  } as Cookie;
  Object.defineProperty(sessionCookie, "value", {
    get() {
      throw new Error("Instagram sessionid value must not be read");
    },
  });
  installChromeMock([sessionCookie]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body || "{}")) as Record<string, unknown>,
    });
    if (String(url).endsWith("/api/config")) {
      return new Response(JSON.stringify({ sources: { instagram: { enabled: true } } }), {
        status: 200,
      });
    }
    return new Response(JSON.stringify({ accepted: true }), { status: 200 });
  };

  assert.equal(await readInstagramLoginState(), true);
  assert.equal(await syncInstagramLoginStateToBackend("test"), true);
  assert.deepEqual(calls, [
    { url: "http://127.0.0.1:8420/api/config", body: {} },
    {
      url: "http://127.0.0.1:8420/api/sources/instagram/credential",
      body: { kind: "login_state", value: true, source: "test" },
    },
  ]);
  assert.doesNotMatch(JSON.stringify(calls), /session-secret/);
});

test("automatic Instagram heartbeat is disabled by config while explicit init bypasses the gate", async () => {
  const { handleCookieSyncRuntimeEvent, syncInstagramLoginStateToBackend } = await importCookieSync();
  installChromeMock([{ name: "sessionid", value: "secret", domain: ".instagram.com" }]);
  const calls: string[] = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    if (String(url).endsWith("/api/config")) {
      return new Response(JSON.stringify({ sources: { instagram: { enabled: false } } }), {
        status: 200,
      });
    }
    return new Response(JSON.stringify({ accepted: true }), { status: 200 });
  };
  assert.equal(await syncInstagramLoginStateToBackend("hourly-alarm"), true);
  assert.deepEqual(calls, ["http://127.0.0.1:8420/api/config"]);
  assert.equal(handleCookieSyncRuntimeEvent({ type: "instagram_login_state_sync_requested" }), true);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(calls.some((url) => url.endsWith("/api/sources/instagram/credential")), true);
});

test("Instagram startup retries unavailable config without cookie access and converges after reconnect", async () => {
  const { startCookieSync, handleCookieSyncAlarm } = await importCookieSync();
  const { alarms } = installChromeMock([]);
  const cookieReads: string[] = [];
  const sessionCookie = { name: "sessionid", domain: ".instagram.com" } as Cookie;
  Object.defineProperty(sessionCookie, "value", {
    get() {
      throw new Error("Instagram sessionid value must not be read");
    },
  });
  chrome.cookies.getAll = async (details) => {
    cookieReads.push(details.domain || "");
    return (details.domain === "instagram.com" ? [sessionCookie] : []) as chrome.cookies.Cookie[];
  };
  chrome.permissions = {
    contains: async () => true,
  } as unknown as typeof chrome.permissions;
  let configAvailable = false;
  const heartbeats: unknown[] = [];
  globalThis.fetch = async (url, init) => {
    if (String(url).endsWith("/api/config")) {
      if (!configAvailable) throw new Error("backend not started yet");
      return new Response(JSON.stringify({ sources: { instagram: { enabled: true } } }));
    }
    if (String(url).endsWith("/api/sources/instagram/credential")) {
      heartbeats.push(JSON.parse(String(init?.body)));
    }
    return new Response(JSON.stringify({ accepted: true }));
  };

  startCookieSync();
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(cookieReads.includes("instagram.com"), false);
  assert.deepEqual(heartbeats, []);
  assert.deepEqual(
    alarms.filter((alarm) => alarm.name === "openbiliclaw-cookie-sync-instagram").at(-1)?.info,
    { delayInMinutes: 1, periodInMinutes: 1 },
  );

  configAvailable = true;
  assert.equal(handleCookieSyncAlarm("openbiliclaw-cookie-sync-instagram"), true);
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.deepEqual(heartbeats, [{ kind: "login_state", value: true, source: "hourly-alarm" }]);
  assert.deepEqual(cookieReads.filter((domain) => domain === "instagram.com"), ["instagram.com"]);
  assert.deepEqual(
    alarms.filter((alarm) => alarm.name === "openbiliclaw-cookie-sync-instagram").at(-1)?.info,
    { periodInMinutes: 60 },
  );
});

for (const failure of ["network", "unauthorized", "unavailable", "malformed-json", "missing-config"]) {
  test(`Instagram ${failure} config keeps cookie access closed and schedules a retry`, async () => {
    const { syncInstagramLoginStateToBackend, isInstagramSourceSyncEnabled } = await importCookieSync();
    const { alarms } = installChromeMock([]);
    let cookieReads = 0;
    chrome.cookies.getAll = async () => {
      cookieReads += 1;
      return [];
    };
    const calls: string[] = [];
    globalThis.fetch = async (url) => {
      calls.push(String(url));
      if (failure === "network") throw new Error("backend unavailable");
      if (failure === "unauthorized") return new Response("", { status: 401 });
      if (failure === "unavailable") return new Response("", { status: 503 });
      if (failure === "malformed-json") return new Response("not-json");
      return new Response(JSON.stringify({ sources: {} }));
    };

    assert.equal(await isInstagramSourceSyncEnabled(), false);
    assert.deepEqual(alarms, [], "readiness probe must not schedule work");
    assert.equal(await syncInstagramLoginStateToBackend("hourly-alarm"), false);
    assert.equal(cookieReads, 0);
    assert.deepEqual(calls, [
      "http://127.0.0.1:8420/api/config",
      "http://127.0.0.1:8420/api/config",
    ]);
    assert.deepEqual(alarms, [{
      name: "openbiliclaw-cookie-sync-instagram",
      info: { delayInMinutes: 1, periodInMinutes: 1 },
    }]);
  });
}

test("Instagram source disabled after an outage returns to hourly checks without reading cookies", async () => {
  const { syncInstagramLoginStateToBackend } = await importCookieSync();
  const { alarms } = installChromeMock([]);
  let cookieReads = 0;
  chrome.cookies.getAll = async () => {
    cookieReads += 1;
    return [];
  };
  globalThis.fetch = async () => new Response("", { status: 503 });
  assert.equal(await syncInstagramLoginStateToBackend("startup"), false);

  globalThis.fetch = async () =>
    new Response(JSON.stringify({ sources: { instagram: { enabled: false } } }));
  assert.equal(await syncInstagramLoginStateToBackend("hourly-alarm"), true);

  assert.equal(cookieReads, 0);
  assert.deepEqual(alarms.map((alarm) => alarm.info), [
    { delayInMinutes: 1, periodInMinutes: 1 },
    { periodInMinutes: 60 },
  ]);
});

test("Instagram permission denial does not read config or cookies and stays on hourly checks", async () => {
  const { syncInstagramLoginStateToBackend } = await importCookieSync();
  const { alarms } = installChromeMock([]);
  let cookieReads = 0;
  chrome.cookies.getAll = async () => {
    cookieReads += 1;
    return [];
  };
  chrome.permissions = {
    contains: async () => false,
  } as unknown as typeof chrome.permissions;
  const calls: string[] = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return new Response(JSON.stringify({ sources: { instagram: { enabled: true } } }));
  };

  assert.equal(await syncInstagramLoginStateToBackend("hourly-alarm"), true);
  assert.equal(cookieReads, 0);
  assert.deepEqual(calls, []);
  assert.deepEqual(alarms, [{
    name: "openbiliclaw-cookie-sync-instagram",
    info: { periodInMinutes: 60 },
  }]);
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
    if (String(url).endsWith("/api/sources/v2ex/credential")) {
      return new Response(JSON.stringify({ accepted: true }), { status: 200 });
    }
    if (String(url).endsWith("/api/sources/instagram/credential")) {
      return new Response(JSON.stringify({ accepted: true }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true, authenticated: true }), { status: 200 });
  };

  const handled = handleCookieSyncAlarm("openbiliclaw-cookie-sync");
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(handled, true);
  // Regression: the bilibili + douyin alarm paths still fire after adding X.
  // (No x.com cookies installed here, so X sends nothing; xhs still reports
  // logged_in=false because login state itself is the synced value.)
  assert.deepEqual(
    calls.map((call) => call.url).sort(),
    [
      "http://127.0.0.1:8420/api/bilibili/cookie",
      "http://127.0.0.1:8420/api/config",
      "http://127.0.0.1:8420/api/sources/dy/cookie",
      "http://127.0.0.1:8420/api/sources/linuxdo/login-state",
      "http://127.0.0.1:8420/api/sources/v2ex/credential",
      "http://127.0.0.1:8420/api/sources/weibo/credential",
      "http://127.0.0.1:8420/api/sources/xhs/login-state",
      "http://127.0.0.1:8420/api/sources/zhihu/login-state",
    ],
  );
  assert.deepEqual(calls.find((call) => call.url.endsWith("/api/sources/dy/cookie"))?.body, {
    cookie: "sessionid=dy-sess; sid_guard=dy-guard; ttwid=dy-tw",
    source: "hourly-alarm",
  });
  assert.deepEqual(calls.find((call) => call.url.endsWith("/api/sources/xhs/login-state"))?.body, {
    logged_in: false,
  });
  assert.deepEqual(calls.find((call) => call.url.endsWith("/api/sources/zhihu/login-state"))?.body, {
    logged_in: false,
  });
  assert.deepEqual(calls.find((call) => call.url.endsWith("/api/sources/linuxdo/login-state"))?.body, {
    logged_in: false,
  });
  assert.deepEqual(calls.find((call) => call.url.endsWith("/api/sources/v2ex/credential"))?.body, {
    kind: "login_state",
    value: false,
    source: "hourly-alarm",
  });
  assert.equal(calls.some((call) => call.url.endsWith("/api/sources/instagram/credential")), false);
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

test("readXhsLoginState returns true when web_session is non-empty", async () => {
  const { readXhsLoginState } = await importCookieSync();
  installChromeMock([
    { name: "web_session", value: "session", domain: ".xiaohongshu.com" },
    { name: "a1", value: "device", domain: ".xiaohongshu.com" },
  ]);

  assert.equal(await readXhsLoginState(), true);
});

test("readXhsLoginState returns false when web_session is absent or empty", async () => {
  const { readXhsLoginState } = await importCookieSync();
  installChromeMock([{ name: "web_session", value: "", domain: ".xiaohongshu.com" }]);
  assert.equal(await readXhsLoginState(), false);

  installChromeMock([{ name: "webId", value: "guest", domain: ".xiaohongshu.com" }]);
  assert.equal(await readXhsLoginState(), false);
});

test("readZhihuLoginState returns true when z_c0 is non-empty", async () => {
  const { readZhihuLoginState } = await importCookieSync();
  installChromeMock([
    { name: "z_c0", value: "token", domain: ".zhihu.com" },
    { name: "_xsrf", value: "guest-xsrf", domain: ".zhihu.com" },
  ]);

  assert.equal(await readZhihuLoginState(), true);
});

test("readZhihuLoginState returns false when z_c0 is absent or empty", async () => {
  const { readZhihuLoginState } = await importCookieSync();
  installChromeMock([{ name: "z_c0", value: "", domain: ".zhihu.com" }]);
  assert.equal(await readZhihuLoginState(), false);

  installChromeMock([
    { name: "_xsrf", value: "guest-xsrf", domain: ".zhihu.com" },
    { name: "d_c0", value: "guest-device", domain: ".zhihu.com" },
  ]);
  assert.equal(await readZhihuLoginState(), false);
});

test("readLinuxdoLoginState recognizes only a non-empty _t login cookie", async () => {
  const { readLinuxdoLoginState } = await importCookieSync();
  installChromeMock([
    { name: "_t", value: "session-secret", domain: ".linux.do" },
    { name: "_forum_session", value: "guest", domain: ".linux.do" },
  ]);
  assert.equal(await readLinuxdoLoginState(), true);

  installChromeMock([{ name: "_forum_session", value: "guest", domain: ".linux.do" }]);
  assert.equal(await readLinuxdoLoginState(), false);
});

test("linuxdo login-state sync sends only a boolean and never the _t value", async () => {
  const { syncLinuxdoLoginStateToBackend } = await importCookieSync();
  installChromeMock([{ name: "_t", value: "must-not-leave-browser", domain: ".linux.do" }]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, logged_in: true }), { status: 200 });
  };

  assert.equal(await syncLinuxdoLoginStateToBackend(), true);
  assert.deepEqual(calls, [{
    url: "http://127.0.0.1:8420/api/sources/linuxdo/login-state",
    body: { logged_in: true },
  }]);
  assert.equal(JSON.stringify(calls).includes("must-not-leave-browser"), false);
});

test("xhs login-state sync posts only the boolean login state", async () => {
  const { syncXhsLoginStateToBackend } = await importCookieSync();
  installChromeMock([
    { name: "web_session", value: "session", domain: ".xiaohongshu.com" },
    { name: "a1", value: "device", domain: ".xiaohongshu.com" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, logged_in: true }), { status: 200 });
  };

  const synced = await syncXhsLoginStateToBackend();

  assert.equal(synced, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/sources/xhs/login-state");
  assert.deepEqual(calls[0].body, { logged_in: true });
});

test("zhihu login-state sync posts only the boolean login state", async () => {
  const { syncZhihuLoginStateToBackend } = await importCookieSync();
  installChromeMock([
    { name: "z_c0", value: "token", domain: ".zhihu.com" },
    { name: "_xsrf", value: "guest-xsrf", domain: ".zhihu.com" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, logged_in: true }), { status: 200 });
  };

  const synced = await syncZhihuLoginStateToBackend();

  assert.equal(synced, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/sources/zhihu/login-state");
  assert.deepEqual(calls[0].body, { logged_in: true });
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

test("runtime events refresh extension-only login state locally", async () => {
  const { handleCookieSyncRuntimeEvent } = await importCookieSync();
  installChromeMock([
    { name: "web_session", value: "xhs-session", domain: ".xiaohongshu.com" },
    { name: "z_c0", value: "zhihu-session", domain: ".zhihu.com" },
    { name: "_t", value: "linuxdo-session", domain: ".linux.do" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, logged_in: true }), { status: 200 });
  };

  assert.equal(
    handleCookieSyncRuntimeEvent({ type: "xhs_login_state_sync_requested" }),
    true,
  );
  assert.equal(
    handleCookieSyncRuntimeEvent({ type: "zhihu_login_state_sync_requested" }),
    true,
  );
  assert.equal(
    handleCookieSyncRuntimeEvent({ type: "linuxdo_login_state_sync_requested" }),
    true,
  );
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.deepEqual(calls, [
    {
      url: "http://127.0.0.1:8420/api/sources/xhs/login-state",
      body: { logged_in: true },
    },
    {
      url: "http://127.0.0.1:8420/api/sources/zhihu/login-state",
      body: { logged_in: true },
    },
    {
      url: "http://127.0.0.1:8420/api/sources/linuxdo/login-state",
      body: { logged_in: true },
    },
  ]);
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
    if (String(url).endsWith("/api/sources/v2ex/credential")) {
      return new Response(JSON.stringify({ accepted: true }), { status: 200 });
    }
    if (String(url).endsWith("/api/sources/instagram/credential")) {
      return new Response(JSON.stringify({ accepted: true }), { status: 200 });
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
      "http://127.0.0.1:8420/api/config",
      "http://127.0.0.1:8420/api/sources/dy/cookie",
      "http://127.0.0.1:8420/api/sources/linuxdo/login-state",
      "http://127.0.0.1:8420/api/sources/v2ex/credential",
      "http://127.0.0.1:8420/api/sources/weibo/credential",
      "http://127.0.0.1:8420/api/sources/x/cookie",
      "http://127.0.0.1:8420/api/sources/xhs/login-state",
      "http://127.0.0.1:8420/api/sources/zhihu/login-state",
    ],
  );
  assert.deepEqual(calls.find((call) => call.url.endsWith("/api/sources/x/cookie"))?.body, {
    cookie: "auth_token=x-at; ct0=x-csrf",
    source: "hourly-alarm",
  });
  assert.deepEqual(calls.find((call) => call.url.endsWith("/api/sources/xhs/login-state"))?.body, {
    logged_in: false,
  });
  assert.deepEqual(calls.find((call) => call.url.endsWith("/api/sources/zhihu/login-state"))?.body, {
    logged_in: false,
  });
  assert.deepEqual(calls.find((call) => call.url.endsWith("/api/sources/linuxdo/login-state"))?.body, {
    logged_in: false,
  });
  assert.equal(calls.some((call) => call.url.endsWith("/api/sources/instagram/credential")), false);
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

test("xhs per-platform alarm posts the latest login state", async () => {
  const { handleCookieSyncAlarm } = await importCookieSync();
  installChromeMock([
    { name: "web_session", value: "session", domain: ".xiaohongshu.com" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, logged_in: true }), { status: 200 });
  };

  const handled = handleCookieSyncAlarm("openbiliclaw-cookie-sync-xhs");
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(handled, true);
  assert.deepEqual(calls, [
    {
      url: "http://127.0.0.1:8420/api/sources/xhs/login-state",
      body: { logged_in: true },
    },
  ]);
});

test("zhihu per-platform alarm posts the latest login state", async () => {
  const { handleCookieSyncAlarm } = await importCookieSync();
  installChromeMock([{ name: "z_c0", value: "token", domain: ".zhihu.com" }]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, logged_in: true }), { status: 200 });
  };

  const handled = handleCookieSyncAlarm("openbiliclaw-cookie-sync-zhihu");
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(handled, true);
  assert.deepEqual(calls, [
    {
      url: "http://127.0.0.1:8420/api/sources/zhihu/login-state",
      body: { logged_in: true },
    },
  ]);
});

test("linuxdo per-platform alarm posts the latest boolean login state", async () => {
  const { handleCookieSyncAlarm } = await importCookieSync();
  installChromeMock([{ name: "_t", value: "session", domain: ".linux.do" }]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, logged_in: true }), { status: 200 });
  };

  assert.equal(handleCookieSyncAlarm("openbiliclaw-cookie-sync-linuxdo"), true);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(calls, [{
    url: "http://127.0.0.1:8420/api/sources/linuxdo/login-state",
    body: { logged_in: true },
  }]);
});

test("startCookieSync triggers a zhihu login-state sync at startup", async () => {
  const { startCookieSync } = await importCookieSync();
  installChromeMock([{ name: "z_c0", value: "token", domain: ".zhihu.com" }]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, logged_in: true }), { status: 200 });
  };

  startCookieSync();
  await new Promise((resolve) => setTimeout(resolve, 0));

  const zhihuCall = calls.find((call) => call.url.endsWith("/api/sources/zhihu/login-state"));
  assert.deepEqual(zhihuCall?.body, { logged_in: true });
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

test("onChanged on a xiaohongshu.com web_session cookie schedules a login-state sync", async () => {
  const { startCookieSync } = await importCookieSync();
  const { listeners } = installChromeMock([
    { name: "web_session", value: "session", domain: ".xiaohongshu.com" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, logged_in: true }), { status: 200 });
  };

  startCookieSync();
  await new Promise((resolve) => setTimeout(resolve, 0));
  calls.length = 0;

  listeners[0]({
    cookie: { name: "web_session", domain: ".xiaohongshu.com" },
    removed: false,
  });
  await new Promise((resolve) => setTimeout(resolve, 2_100));

  const xhsCall = calls.find((call) => call.url.endsWith("/api/sources/xhs/login-state"));
  assert.deepEqual(xhsCall?.body, { logged_in: true });
});

test("onChanged on a zhihu.com z_c0 cookie schedules a login-state sync", async () => {
  const { startCookieSync } = await importCookieSync();
  const { listeners } = installChromeMock([{ name: "z_c0", value: "token", domain: ".zhihu.com" }]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, logged_in: true }), { status: 200 });
  };

  startCookieSync();
  await new Promise((resolve) => setTimeout(resolve, 0));
  calls.length = 0;

  listeners[0]({
    cookie: { name: "z_c0", domain: ".zhihu.com" },
    removed: false,
  });
  await new Promise((resolve) => setTimeout(resolve, 2_100));

  const zhihuCall = calls.find((call) => call.url.endsWith("/api/sources/zhihu/login-state"));
  assert.deepEqual(zhihuCall?.body, { logged_in: true });
});

test("onChanged on linux.do _t schedules boolean login-state sync", async () => {
  const { startCookieSync } = await importCookieSync();
  const { listeners } = installChromeMock([
    { name: "_t", value: "secret-session", domain: ".linux.do" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, logged_in: true }), { status: 200 });
  };

  startCookieSync();
  await new Promise((resolve) => setTimeout(resolve, 0));
  calls.length = 0;
  listeners[0]({ cookie: { name: "_t", domain: ".linux.do" }, removed: false });
  await new Promise((resolve) => setTimeout(resolve, 2_100));

  const call = calls.find((item) => item.url.endsWith("/api/sources/linuxdo/login-state"));
  assert.deepEqual(call?.body, { logged_in: true });
  assert.equal(JSON.stringify(call).includes("secret-session"), false);
});

test("cookieDomainMatchesSite accepts bare, dotted, and subdomain cookie domains", async () => {
  const { cookieDomainMatchesSite } = await importCookieSync();
  assert.equal(cookieDomainMatchesSite("bilibili.com", "bilibili.com"), true);
  assert.equal(cookieDomainMatchesSite(".bilibili.com", "bilibili.com"), true);
  assert.equal(cookieDomainMatchesSite("www.bilibili.com", "bilibili.com"), true);
  assert.equal(cookieDomainMatchesSite("passport.bilibili.com", "bilibili.com"), true);
  assert.equal(cookieDomainMatchesSite("notbilibili.com", "bilibili.com"), false);
  assert.equal(cookieDomainMatchesSite("bilibili.com.evil.com", "bilibili.com"), false);
  assert.equal(cookieDomainMatchesSite("weibo.cn", "weibo.cn"), true);
  assert.equal(cookieDomainMatchesSite("www.weibo.cn", "weibo.cn"), true);
});

test("readBilibiliCookieHeader reads subdomain cookies even when Safari domain filter is exact", async () => {
  const { readBilibiliCookieHeader } = await importCookieSync();
  // Simulate Safari's exact-domain cookies.getAll({domain}) semantics: a
  // domain-filtered call for "bilibili.com" would only return cookies whose
  // domain equals "bilibili.com", missing the real .bilibili.com session jar.
  globalThis.chrome = {
    cookies: {
      getAll: async (details?: { domain?: string }) => {
        if (!details?.domain) {
          return [
            { name: "SESSDATA", value: "sess", domain: ".bilibili.com" },
            { name: "bili_jct", value: "csrf", domain: ".bilibili.com" },
            { name: "DedeUserID", value: "42", domain: ".bilibili.com" },
            { name: "buvid3", value: "anonymous", domain: "www.bilibili.com" },
            { name: "SESSDATA", value: "sess", domain: "bilibili.com" },
            { name: "bili_jct", value: "csrf", domain: "bilibili.com" },
            { name: "DedeUserID", value: "42", domain: "bilibili.com" },
          ];
        }
        return [];
      },
    },
  } as unknown as typeof chrome;
  const header = await readBilibiliCookieHeader();
  assert.ok(header);
  assert.match(header, /SESSDATA=sess/);
  assert.match(header, /bili_jct=csrf/);
  assert.match(header, /DedeUserID=42/);
});

test("readCookiesForDomains falls back to per-domain getAll when unfiltered getAll throws", async () => {
  const { readWeiboLoginState } = await importCookieSync();
  globalThis.chrome = {
    cookies: {
      getAll: async (details?: { domain?: string }) => {
        if (!details?.domain) {
          throw new Error("Safari rejects unfiltered getAll");
        }
        if (details.domain === "weibo.com") {
          return [
            { name: "SUBP", value: "subp", domain: ".weibo.com" },
            { name: "ALF", value: "alf", domain: ".weibo.com" },
          ];
        }
        if (details.domain === "weibo.cn") {
          return [{ name: "SUBP", value: "subp", domain: ".weibo.cn" }];
        }
        return [];
      },
    },
  } as unknown as typeof chrome;
  assert.equal(await readWeiboLoginState(), true);
});

test("readTiktokCookieHeader returns the full jar once a session cookie exists", async () => {
  const { readTiktokCookieHeader } = await importCookieSync();
  installChromeMock([
    { name: "sessionid", value: "tt-sess", domain: ".tiktok.com" },
    { name: "ttwid", value: "tt-tw", domain: ".tiktok.com" },
    { name: "msToken", value: "tt-ms", domain: ".tiktok.com" },
  ]);

  assert.equal(await readTiktokCookieHeader(), "sessionid=tt-sess; ttwid=tt-tw; msToken=tt-ms");
});

test("readTiktokCookieHeader never pushes a guest jar (ttwid / msToken only)", async () => {
  const { readTiktokCookieHeader } = await importCookieSync();
  installChromeMock([
    { name: "ttwid", value: "tt-tw", domain: ".tiktok.com" },
    { name: "msToken", value: "tt-ms", domain: ".tiktok.com" },
  ]);

  // Guest cookies are not a login — guest identity works without them, so
  // there is nothing worth a round trip.
  assert.equal(await readTiktokCookieHeader(), null);

  installChromeMock([]);
  assert.equal(await readTiktokCookieHeader(), null);
});

test("cookie sync runtime event posts the current tiktok cookie immediately", async () => {
  const { handleCookieSyncRuntimeEvent } = await importCookieSync();
  installChromeMock([
    { name: "sessionid", value: "tt-sess", domain: ".tiktok.com" },
    { name: "ttwid", value: "tt-tw", domain: ".tiktok.com" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ accepted: true }), { status: 200 });
  };

  const handled = handleCookieSyncRuntimeEvent({
    type: "tiktok_cookie_sync_requested",
    reason: "missing_cookie",
  });
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(handled, true);
  assert.equal(calls.length, 1);
  // The unified credential write gate, same path as the settings paste box.
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/sources/tiktok/credential");
  assert.deepEqual(calls[0].body, {
    value: "sessionid=tt-sess; ttwid=tt-tw",
    source: "runtime-stream-request",
  });
});

test("onChanged on tiktok.com session cookies debounces into one sync", async () => {
  const { startCookieSync } = await importCookieSync();
  const { listeners } = installChromeMock([
    { name: "sessionid", value: "tt-sess", domain: ".tiktok.com" },
    { name: "ttwid", value: "tt-tw", domain: ".tiktok.com" },
  ]);
  const calls: string[] = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return new Response(JSON.stringify({ accepted: true }), { status: 200 });
  };

  startCookieSync();
  await new Promise((resolve) => setTimeout(resolve, 0));
  calls.length = 0;

  // A login touches several cookies; the debounce must collapse them.
  listeners[0]({ cookie: { name: "sessionid", domain: ".tiktok.com" }, removed: false });
  listeners[0]({ cookie: { name: "ttwid", domain: ".tiktok.com" }, removed: false });
  listeners[0]({ cookie: { name: "msToken", domain: ".tiktok.com" }, removed: false });
  // Analytics churn must not trigger anything.
  listeners[0]({ cookie: { name: "tt_csrf_token", domain: ".tiktok.com" }, removed: false });
  await new Promise((resolve) => setTimeout(resolve, 2_100));

  assert.deepEqual(calls, ["http://127.0.0.1:8420/api/sources/tiktok/credential"]);
});

test("tiktok logout syncs silently (no POST, no error)", async () => {
  const { startCookieSync } = await importCookieSync();
  // The session cookie is gone from the jar by the time the sync runs.
  const { listeners } = installChromeMock([
    { name: "ttwid", value: "tt-tw", domain: ".tiktok.com" },
  ]);
  const calls: string[] = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return new Response(JSON.stringify({ accepted: true }), { status: 200 });
  };

  startCookieSync();
  await new Promise((resolve) => setTimeout(resolve, 0));
  calls.length = 0;

  listeners[0]({ cookie: { name: "sessionid", domain: ".tiktok.com" }, removed: true });
  await new Promise((resolve) => setTimeout(resolve, 2_100));

  // Guest mode is legitimate: a logout must not pester the backend.
  assert.deepEqual(calls, []);
});

test("tiktok sync retry cadence follows the backend verdict", async () => {
  const { syncTiktokCookieToBackend } = await importCookieSync();
  const { alarms } = installChromeMock([
    { name: "sessionid", value: "tt-sess", domain: ".tiktok.com" },
  ]);

  // Passport probe refuses the jar: wait for a real re-login (hourly).
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ accepted: false, error_code: "cookie_invalid" }), {
      status: 200,
    });
  assert.equal(await syncTiktokCookieToBackend("test"), false);
  assert.ok(
    alarms.some(
      (alarm) =>
        alarm.name === "openbiliclaw-cookie-sync-tiktok" && alarm.info.delayInMinutes === 60,
    ),
  );

  // Probe could not conclude (network): moderate 5-minute retry.
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ accepted: false, error_code: "validation_network" }), {
      status: 200,
    });
  assert.equal(await syncTiktokCookieToBackend("test"), false);
  assert.ok(
    alarms.some(
      (alarm) =>
        alarm.name === "openbiliclaw-cookie-sync-tiktok" && alarm.info.delayInMinutes === 5,
    ),
  );

  // Accepted: settle into the hourly refresh.
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ accepted: true }), { status: 200 });
  assert.equal(await syncTiktokCookieToBackend("test"), true);
  assert.ok(
    alarms.some(
      (alarm) =>
        alarm.name === "openbiliclaw-cookie-sync-tiktok" && alarm.info.periodInMinutes === 60,
    ),
  );
});

test("tiktok per-platform alarm syncs only tiktok", async () => {
  const { handleCookieSyncAlarm } = await importCookieSync();
  installChromeMock([
    { name: "sessionid", value: "tt-sess", domain: ".tiktok.com" },
    { name: "sessionid", value: "dy-sess", domain: ".douyin.com" },
  ]);
  const calls: string[] = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return new Response(
      JSON.stringify({ ok: true, has_cookie: true, accepted: true }),
      { status: 200 },
    );
  };

  const handled = handleCookieSyncAlarm("openbiliclaw-cookie-sync-tiktok");
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(handled, true);
  assert.deepEqual(calls, ["http://127.0.0.1:8420/api/sources/tiktok/credential"]);
});
