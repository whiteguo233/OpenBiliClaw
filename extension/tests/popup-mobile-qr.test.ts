import assert from "node:assert/strict";
import test from "node:test";

import {
  buildMobileWebUrl,
  createQrSvgMarkup,
  getMobileQrViewState,
  isLoopbackMobileHost,
} from "../popup/popup-qr.js";

test("mobile web QR state uses the configured backend endpoint", () => {
  assert.equal(
    buildMobileWebUrl({ host: "192.168.1.100", port: 8420 }),
    "http://192.168.1.100:8420/m/",
  );

  const state = getMobileQrViewState({ host: "192.168.1.100", port: 19090 });
  assert.equal(state.url, "http://192.168.1.100:19090/m/");
  assert.equal(state.tone, "info");
  assert.match(state.hint, /同一个局域网/);
});

test("mobile web QR state warns when the configured host is loopback", () => {
  assert.equal(isLoopbackMobileHost("127.0.0.1"), true);
  assert.equal(isLoopbackMobileHost("localhost"), true);
  assert.equal(isLoopbackMobileHost("192.168.1.100"), false);

  const state = getMobileQrViewState({ host: "127.0.0.1", port: 8420 });
  assert.equal(state.url, "http://127.0.0.1:8420/m/");
  assert.equal(state.tone, "warning");
  assert.match(state.hint, /局域网 IP/);
});

test("mobile web QR SVG is generated locally without embedding a remote service URL", () => {
  const svg = createQrSvgMarkup("http://192.168.1.100:8420/m/");

  assert.match(svg, /^<svg /);
  assert.match(svg, /viewBox="0 0 45 45"/);
  assert.match(svg, /<path d="M/);
  assert.doesNotMatch(svg, /api\.qrserver|chart\.googleapis|192\.168\.1\.100/);
});
