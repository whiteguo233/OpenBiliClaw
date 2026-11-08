import assert from "node:assert/strict";
import test from "node:test";

import { __resetPopupDeviceAuthForTests } from "../popup/popup-device-auth.js";
import { initExtLogin } from "../popup/popup-ext-login.js";

// 本文件是 `checkAuthStatus` 的可复用正确性测试，锁定以下行为契约，后续改动必须满足：
//   1. 认证状态以服务端 `/auth/status` 返回的 `data.authenticated` 为唯一权威。
//   2. 本地缓存的未过期 token 不得覆盖服务器判断——即使本地有旧 token，
//      只要服务端说未认证，popup 就必须显示配对提示并展示输入栏（而不是谎报"已配对"）。
//   3. 服务端不可达时显示"无法连接后端"并展示输入栏。
// 回归守卫：若 `popup-ext-login.js` 回退为 `data.authenticated || readPopupSessionToken()`，
// 用例 1 会落入"设备已配对"分支，断言将失败。

function storageHarness(initial: Record<string, unknown> = {}) {
  const values = { ...initial };
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  (globalThis as { chrome?: unknown }).chrome = {
    storage: { local: {
      get(keys: string | string[], callback: (items: Record<string, unknown>) => void) {
        const selected = Array.isArray(keys) ? keys : [keys];
        callback(Object.fromEntries(selected.filter((k) => k in values).map((k) => [k, values[k]])));
      },
      set(items: Record<string, unknown>, callback: () => void) {
        Object.assign(values, items);
        callback();
      },
      remove(keys: string | string[], callback: () => void) {
        for (const k of Array.isArray(keys) ? keys : [keys]) delete values[k];
        callback();
      },
    } },
  };
  __resetPopupDeviceAuthForTests();
  return { values, restore() {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    __resetPopupDeviceAuthForTests();
  } };
}

function fakeEls() {
  return {
    status: { textContent: "", style: { color: "" } },
    deviceKey: { hidden: true, value: "k", addEventListener() {} },
    btn: { hidden: true, addEventListener() {} },
  };
}

test("remote setup is required before pairing when password access is disabled", async () => {
  const storage = storageHarness();
  const els = fakeEls();
  try {
    const control = initExtLogin(els, {
      getBaseUrl: async () => "https://backend.example/api",
      fetchImpl: async () => Response.json({
        enabled: false, authenticated: false, setup_required: true,
      }),
    });
    await control.checkAuthStatus();
    assert.equal(els.status.textContent, "请先在后端电脑上启用访问密码，再配置扩展设备密钥");
    assert.equal(els.deviceKey.hidden, true);
    assert.equal(els.btn.hidden, true);
    assert.equal(els.status.style.color, "#ef7a86");
  } finally {
    storage.restore();
  }
});

test("trusted local callers do not need remote device pairing", async () => {
  const storage = storageHarness();
  const els = fakeEls();
  try {
    const control = initExtLogin(els, {
      getBaseUrl: async () => "http://127.0.0.1:8420/api",
      fetchImpl: async () => Response.json({
        enabled: false, authenticated: true, setup_required: false,
      }),
    });
    await control.checkAuthStatus();
    assert.equal(els.status.textContent, "本机访问无需配对");
    assert.equal(els.deviceKey.hidden, true);
    assert.equal(els.status.style.color, "#30b980");
  } finally {
    storage.restore();
  }
});

test("checkAuthStatus shows pairing prompt when server reports not authenticated despite a cached token", async () => {
  const storage = storageHarness({
    obc_auth_session: { token: "old-token", expires_at: 2_000_000_000 },
  });
  const els = fakeEls();
  const checkAuthStatus = initExtLogin(els, {
    getBaseUrl: async () => "https://backend.example/api",
    fetchImpl: async (url: string) => {
      if (String(url).endsWith("/auth/status")) {
        return Response.json({ enabled: true, authenticated: false });
      }
      return new Response("", { status: 200 });
    },
  }).checkAuthStatus;
  try {
    await checkAuthStatus();
    assert.equal(els.status.textContent, "需要设备访问密钥");
    assert.equal(els.deviceKey.hidden, false);
    assert.equal(els.btn.hidden, false);
  } finally {
    storage.restore();
  }
});

test("checkAuthStatus shows paired when server reports authenticated", async () => {
  const storage = storageHarness({
    obc_auth_session: { token: "valid-token", expires_at: 2_000_000_000 },
  });
  const els = fakeEls();
  const checkAuthStatus = initExtLogin(els, {
    getBaseUrl: async () => "https://backend.example/api",
    fetchImpl: async (url: string) => {
      if (String(url).endsWith("/auth/status")) {
        return Response.json({ enabled: true, authenticated: true });
      }
      return new Response("", { status: 200 });
    },
  }).checkAuthStatus;
  try {
    await checkAuthStatus();
    assert.equal(els.status.textContent, "设备已配对");
    assert.equal(els.deviceKey.hidden, true);
    assert.equal(els.btn.hidden, true);
  } finally {
    storage.restore();
  }
});

test("checkAuthStatus shows unreachable on non-ok response", async () => {
  const storage = storageHarness();
  const els = fakeEls();
  const checkAuthStatus = initExtLogin(els, {
    getBaseUrl: async () => "https://backend.example/api",
    fetchImpl: async () => new Response("", { status: 500 }),
  }).checkAuthStatus;
  try {
    await checkAuthStatus();
    assert.equal(els.status.textContent, "无法连接后端");
    assert.equal(els.deviceKey.hidden, false);
    assert.equal(els.btn.hidden, false);
  } finally {
    storage.restore();
  }
});
