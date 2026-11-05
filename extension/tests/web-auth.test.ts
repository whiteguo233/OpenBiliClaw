import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

function installFetchMock() {
  const calls: any[] = [];
  const events: string[] = [];
  (globalThis as any).location = new URL("http://127.0.0.1:8420/m/");
  (globalThis as any).window = {
    dispatchEvent(ev: any) {
      events.push(ev?.type ?? "");
      return true;
    },
  };
  (globalThis as any).CustomEvent = class {
    type: string;
    constructor(type: string) {
      this.type = type;
    }
  };
  return { calls, events };
}

test("mobile api exposes auth helpers", async () => {
  const { calls } = installFetchMock();
  (globalThis as any).fetch = async (url: string, options: any = {}) => {
    calls.push({ url, options });
    return { ok: true, status: 200, async json() {
      return { enabled: true, authenticated: false };
    } };
  };
  const api = await import("../../src/openbiliclaw/web/js/api.js?auth-helpers");
  assert.equal(typeof api.fetchAuthStatus, "function");
  assert.equal(typeof api.login, "function");
  assert.equal(typeof api.logout, "function");

  const status = await api.fetchAuthStatus();
  assert.deepEqual(status, { enabled: true, authenticated: false });
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/auth/status");
  assert.equal(calls[0].options.credentials, "same-origin");
});

test("login posts password with credentials and returns ok", async () => {
  const { calls } = installFetchMock();
  (globalThis as any).fetch = async (url: string, options: any = {}) => {
    calls.push({ url, options });
    return { ok: true, status: 200, async json() {
      return { ok: true };
    } };
  };
  const api = await import("../../src/openbiliclaw/web/js/api.js?auth-login");
  const result = await api.login("hunter2");
  assert.equal(result.ok, true);
  const call = calls.find((c) => String(c.url).endsWith("/api/auth/login"));
  assert.ok(call, "login should hit /api/auth/login");
  assert.equal(call.options.method, "POST");
  assert.equal(call.options.credentials, "same-origin");
  assert.deepEqual(JSON.parse(call.options.body), { password: "hunter2" });
});

test("state-changing requests carry the CSRF header; reads do not", async () => {
  const { calls } = installFetchMock();
  (globalThis as any).fetch = async (url: string, options: any = {}) => {
    calls.push({ url, options });
    return { ok: true, status: 200, async json() {
      return { saved: true };
    } };
  };
  const api = await import("../../src/openbiliclaw/web/js/api.js?auth-csrf");
  await api.addToFavorite("BV1AUTH"); // POST
  await api.favoriteStatus("BV1AUTH"); // GET
  const post = calls[0];
  const get = calls[1];
  assert.equal(post.options.method, "POST");
  assert.equal(post.options.headers["X-OBC-Auth"], "1");
  assert.equal(post.options.credentials, "same-origin");
  // GET also carries the CSRF header now, so state-changing GETs
  // (e.g. /api/recommendations) pass the gate uniformly.
  assert.equal(get.options.headers["X-OBC-Auth"], "1");
  assert.equal(get.options.credentials, "same-origin");
});

test("a 401 response dispatches the auth-required event", async () => {
  const { events } = installFetchMock();
  (globalThis as any).fetch = async () => {
    return { ok: false, status: 401, async json() {
      return { error: "auth_required" };
    } };
  };
  const api = await import("../../src/openbiliclaw/web/js/api.js?auth-401");
  await assert.rejects(() => api.fetchRecommendations());
  assert.ok(events.includes("obc:auth-required"), "should dispatch obc:auth-required on 401");
});

test("mobile shell gates boot on auth status and renders a login view", () => {
  const appJs = readFileSync(resolve("../src/openbiliclaw/web/js/app.js"), "utf8");
  assert.match(appJs, /fetchAuthStatus/);
  assert.match(appJs, /renderLoginView/);
  assert.match(appJs, /obc:auth-required/);
  assert.match(appJs, /auth-locked/);

  const loginJs = readFileSync(resolve("../src/openbiliclaw/web/js/views/login.js"), "utf8");
  assert.match(loginJs, /export function renderLoginView/);
  assert.match(loginJs, /login\(password\)/);

  // login success reloads (clean re-boot) rather than re-running startApp() in
  // place, which would leave the login view / detached cached views (review r5).
  assert.match(appJs, /onSuccess\(\)\s*\{\s*location\.reload\(\)/);
});

test("desktop web wires the password gate (credentials, CSRF, login overlay)", () => {
  const desktopJs = readFileSync(
    resolve("../src/openbiliclaw/web/desktop/assets/js/app.js"),
    "utf8",
  );
  // same-origin credentials + CSRF header on state-changing requests
  assert.match(desktopJs, /credentials\s*=\s*"same-origin"/);
  assert.match(desktopJs, /"X-OBC-Auth"/);
  // relative default base (correct under HTTPS proxy / PWA)
  assert.match(desktopJs, /return "\/api";/);
  // login overlay + boot gate + 401 handling
  assert.match(desktopJs, /function showLoginOverlay/);
  assert.match(desktopJs, /function ensureAuthenticated/);
  assert.match(desktopJs, /handleAuthRequired\(\)/);
  assert.match(desktopJs, /auth\/login/);
  // cross-origin bearer mode: token stored + attached to fetch/WS/image
  assert.match(desktopJs, /function isCrossOriginBase/);
  assert.match(desktopJs, /sessionStorage/);
  assert.match(desktopJs, /function withBearer/);
  assert.match(desktopJs, /function appendToken/);
  assert.match(desktopJs, /if \(data\.token\) setSessionToken\(data\.token\)/);
  // cross-origin cover images mark crossorigin so Origin is sent and ?token= is honored
  assert.match(desktopJs, /function imgCrossOriginAttr/);
  assert.match(desktopJs, /crossOrigin = "anonymous"/);
  // all fetches (incl GET) carry the CSRF header so mutating GETs pass uniformly
  assert.doesNotMatch(desktopJs, /method !== "GET" && method !== "HEAD"/);
});
